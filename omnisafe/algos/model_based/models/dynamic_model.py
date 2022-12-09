# Copyright 2022 OmniSafe Team. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
# Modified version of model.py from  https://github.com/Xingyu-Lin/mbpo_pytorch/blob/main/model.py
# original version doesn't validate model error batch-wise and is highly memory intensive.
# ==============================================================================
"""Dynamics Model"""

import itertools

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class StandardScaler:
    """Normalize data"""

    def __init__(self, device=torch.device('cpu')):
        self.mean = 0.0
        self.std = 1.0
        self.mean_t = torch.tensor(self.mean).to(device)
        self.std_t = torch.tensor(self.std).to(device)
        self.device = device

    def fit(self, data):
        """Runs two ops, one for assigning the mean of the data to the internal mean, and
        another for assigning the standard deviation of the data to the internal standard deviation.
        This function must be called within a 'with <session>.as_default()' block.

        Arguments:
        data (np.ndarray): A numpy array containing the input

        Returns: None.
        """
        self.mean = np.mean(data, axis=0, keepdims=True)
        self.std = np.std(data, axis=0, keepdims=True)
        self.std[self.std < 1e-12] = 1.0
        self.mean_t = torch.FloatTensor(self.mean).to(self.device)
        self.std_t = torch.FloatTensor(self.std).to(self.device)

    def transform(self, data):
        """Transforms the input matrix data using the parameters of this scaler.

        Arguments:
        data (np.array): A numpy array containing the points to be transformed.

        Returns: (np.array) The transformed dataset.
        """
        if torch.is_tensor(data):
            return (data - self.mean_t) / self.std_t
        return (data - self.mean) / self.std


def init_weights(layer):
    """Initialize network weight"""

    def truncated_normal_init(weight, mean=0.0, std=0.01):
        """Initialize network weight"""
        torch.nn.init.normal_(weight, mean=mean, std=std)
        while True:
            cond = torch.logical_or(weight < mean - 2 * std, weight > mean + 2 * std)
            if not torch.sum(cond):
                break
            weight = torch.where(
                cond, torch.nn.init.normal_(torch.ones(weight.shape), mean=mean, std=std), weight
            )
        return weight

    if type(layer) == nn.Linear or isinstance(layer, EnsembleFC):
        input_dim = layer.in_features
        truncated_normal_init(layer.weight, std=1 / (2 * np.sqrt(input_dim)))
        layer.bias.data.fill_(0.0)


class EnsembleFC(nn.Module):
    """Ensemble fully connected network"""

    __constants__ = ['in_features', 'out_features']
    in_features: int
    out_features: int
    ensemble_size: int
    weight: torch.Tensor

    def __init__(
        self,
        in_features: int,
        out_features: int,
        ensemble_size: int,
        weight_decay: float = 0.0,
        bias: bool = True,
    ) -> None:  # pylint: disable-next=too-many-arguments
        super(EnsembleFC, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.ensemble_size = ensemble_size
        self.weight = nn.Parameter(torch.Tensor(ensemble_size, in_features, out_features))
        self.weight_decay = weight_decay
        if bias:
            self.bias = nn.Parameter(torch.Tensor(ensemble_size, out_features))
        else:
            self.register_parameter('bias', None)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        """reset parameters"""
        pass

    def forward(self, input_data: torch.Tensor) -> torch.Tensor:
        """forward"""
        w_times_x = torch.bmm(input_data, self.weight)
        return torch.add(w_times_x, self.bias[:, None, :])  # w times x + b

    def extra_repr(self) -> str:
        """Output weight"""
        return 'in_features={}, out_features={}, bias={}'.format(
            self.in_features, self.out_features, self.bias is not None
        )


class EnsembleModel(nn.Module):
    """Ensemble dynamics model"""

    def __init__(
        self,
        algo,
        state_size,
        action_size,
        reward_size,
        cost_size,
        ensemble_size,
        hidden_size=200,
        learning_rate=1e-3,
        use_decay=False,
    ):
        super(EnsembleModel, self).__init__()
        self.algo = algo
        if self.algo == 'mbppo-lag':
            self.output_dim = state_size
        elif self.algo == 'safe-loop':
            self.output_dim = state_size + reward_size
        self.hidden_size = hidden_size
        self.use_decay = use_decay

        self.nn1 = EnsembleFC(
            state_size + action_size, hidden_size, ensemble_size, weight_decay=0.000025
        )
        self.nn2 = EnsembleFC(hidden_size, hidden_size, ensemble_size, weight_decay=0.00005)
        self.nn3 = EnsembleFC(hidden_size, hidden_size, ensemble_size, weight_decay=0.000075)
        self.nn4 = EnsembleFC(hidden_size, hidden_size, ensemble_size, weight_decay=0.000075)
        if self.algo in ['mbppo-lag', 'safe-loop']:
            self.nn5 = EnsembleFC(
                hidden_size, self.output_dim * 2, ensemble_size, weight_decay=0.0001
            )

        self.register_buffer('max_logvar', (torch.ones((1, self.output_dim)).float() / 2))
        self.register_buffer('min_logvar', (-torch.ones((1, self.output_dim)).float() * 10))
        self.optimizer = torch.optim.Adam(self.parameters(), lr=learning_rate)
        self.apply(init_weights)
        self.swish = Swish()

    def forward(self, data, ret_log_var=False):
        nn1_output = self.swish(self.nn1(data))
        nn2_output = self.swish(self.nn2(nn1_output))
        nn3_output = self.swish(self.nn3(nn2_output))
        nn4_output = self.swish(self.nn4(nn3_output))
        nn5_output = self.nn5(nn4_output)

        mean = nn5_output[:, :, : self.output_dim]

        logvar = self.max_logvar - F.softplus(self.max_logvar - nn5_output[:, :, self.output_dim :])
        logvar = self.min_logvar + F.softplus(logvar - self.min_logvar)

        if ret_log_var:
            return mean, logvar
        return mean, torch.exp(logvar)

    def get_decay_loss(self):
        """Get decay loss"""
        decay_loss = 0.0
        for layer in self.children():
            if isinstance(layer, EnsembleFC):
                decay_loss += layer.weight_decay * torch.sum(torch.square(layer.weight)) / 2.0
        return decay_loss

    def loss(self, mean, logvar, labels, inc_var_loss=True):
        """
        mean, logvar: Ensemble_size x N x dim
        labels: N x dim
        """
        assert len(mean.shape) == len(logvar.shape) == len(labels.shape) == 3
        inv_var = torch.exp(-logvar)
        if inc_var_loss:
            # Average over batch and dim, sum over ensembles.
            mse_loss = torch.mean(torch.mean(torch.pow(mean - labels, 2) * inv_var, dim=-1), dim=-1)
            var_loss = torch.mean(torch.mean(logvar, dim=-1), dim=-1)
            total_loss = torch.sum(mse_loss) + torch.sum(var_loss)
        else:
            mse_loss = torch.mean(torch.pow(mean - labels, 2), dim=(1, 2))
            total_loss = torch.sum(mse_loss)
        return total_loss, mse_loss

    def train(self, loss):
        self.optimizer.zero_grad()
        loss += 0.01 * torch.sum(self.max_logvar) - 0.01 * torch.sum(self.min_logvar)
        if self.use_decay:
            loss += self.get_decay_loss()
        loss.backward()
        self.optimizer.step()


class EnsembleDynamicsModel:
    def __init__(
        self,
        algo,
        device,
        network_size,
        elite_size,
        hidden_size,
        use_decay,
        state_size,
        action_size,
        reward_size=0,
        cost_size=0,
    ):
        self.algo = algo
        self.network_size = network_size
        self.elite_size = elite_size
        self.model_list = []
        self.state_size = state_size
        self.action_size = action_size
        self.reward_size = reward_size
        self.cost_size = cost_size
        self.network_size = network_size
        self.device = device
        if self.algo == 'mbppo-lag':
            self.elite_model_idxes = []

        elif self.algo == 'safe-loop':
            self.elite_model_idxes = [0, 1, 2, 3, 4]

        self.ensemble_model = EnsembleModel(
            algo,
            state_size,
            action_size,
            reward_size,
            cost_size,
            network_size,
            hidden_size,
            use_decay=use_decay,
        )
        self.ensemble_model.to(self.device)
        self.scaler = StandardScaler(self.device)

    def train(self, inputs, labels, batch_size=256, holdout_ratio=0.0, max_epochs_since_update=5):
        self._max_epochs_since_update = max_epochs_since_update
        self._epochs_since_update = 0
        self._state = {}
        self._snapshots = {i: (None, 1e10) for i in range(self.network_size)}

        num_holdout = int(inputs.shape[0] * holdout_ratio)
        permutation = np.random.permutation(inputs.shape[0])
        inputs, labels = inputs[permutation], labels[permutation]

        # split training and testing dataset
        train_inputs, train_labels = inputs[num_holdout:], labels[num_holdout:]
        holdout_inputs, holdout_labels = inputs[:num_holdout], labels[:num_holdout]

        self.scaler.fit(train_inputs)
        train_inputs = self.scaler.transform(train_inputs)
        holdout_inputs = self.scaler.transform(holdout_inputs)
        if self.algo == 'safe-loop':
            holdout_inputs = torch.from_numpy(holdout_inputs).float().to(self.device)
            holdout_labels = torch.from_numpy(holdout_labels).float().to(self.device)
            holdout_inputs = holdout_inputs[None, :, :].repeat([self.network_size, 1, 1])
            holdout_labels = holdout_labels[None, :, :].repeat([self.network_size, 1, 1])

        for epoch in itertools.count():
            # training
            train_idx = np.vstack(
                [np.random.permutation(train_inputs.shape[0]) for _ in range(self.network_size)]
            )
            # shape: [train_inputs.shape[0],network_size]

            losses = []
            for start_pos in range(0, train_inputs.shape[0], batch_size):
                idx = train_idx[:, start_pos : start_pos + batch_size]
                train_input = torch.from_numpy(train_inputs[idx]).float().to(self.device)
                train_label = torch.from_numpy(train_labels[idx]).float().to(self.device)
                mean, logvar = self.ensemble_model(train_input, ret_log_var=True)
                loss, mtrain = self.ensemble_model.loss(mean, logvar, train_label)
                self.ensemble_model.train(loss)
                if self.algo == 'mbppo-lag':
                    losses.append(mtrain)
                elif self.algo == 'safe-loop':
                    losses.append(loss)
            if self.algo == 'mbppo-lag':
                # validation
                val_idx = np.vstack(
                    [
                        np.random.permutation(holdout_inputs.shape[0])
                        for _ in range(self.network_size)
                    ]
                )
                val_batch_size = 512
                val_losses_list = []
                len_valid = 0
                for start_pos in range(0, holdout_inputs.shape[0], val_batch_size):
                    with torch.no_grad():
                        idx = val_idx[:, start_pos : start_pos + val_batch_size]
                        val_input = torch.from_numpy(holdout_inputs[idx]).float().to(self.device)
                        val_label = torch.from_numpy(holdout_labels[idx]).float().to(self.device)
                        holdout_mean, holdout_logvar = self.ensemble_model(
                            val_input, ret_log_var=True
                        )
                        _, holdout_mse_losses = self.ensemble_model.loss(
                            holdout_mean, holdout_logvar, val_label, inc_var_loss=False
                        )
                        holdout_mse_losses = holdout_mse_losses.detach().cpu().numpy()
                        val_losses_list.append(holdout_mse_losses)
                    len_valid += 1
                val_losses = np.array(val_losses_list)
                val_losses = np.sum(val_losses, axis=0) / len_valid
                sorted_loss_idx = np.argsort(val_losses)
                self.elite_model_idxes = sorted_loss_idx[: self.elite_size].tolist()
                break_train = self._save_best(epoch, val_losses)
                if break_train:
                    break
                train_mse_losses = []
                for i in losses:
                    train_mse_losses.append(i.detach().cpu().numpy())

            elif self.algo == 'safe-loop':
                with torch.no_grad():
                    holdout_mean, holdout_logvar = self.ensemble_model(
                        holdout_inputs, ret_log_var=True
                    )
                    _, holdout_mse_losses = self.ensemble_model.loss(
                        holdout_mean, holdout_logvar, holdout_labels, inc_var_loss=False
                    )
                    holdout_mse_losses = holdout_mse_losses.detach().cpu().numpy()
                    sorted_loss_idx = np.argsort(holdout_mse_losses)
                    self.elite_model_idxes = sorted_loss_idx[: self.elite_size].tolist()
                    break_train = self._save_best(epoch, holdout_mse_losses)
                    if break_train:
                        break
        if self.algo == 'safe-loop':
            return 0, holdout_mse_losses.mean()

    def _save_best(self, epoch, holdout_losses):
        updated = False
        for i in range(len(holdout_losses)):
            current = holdout_losses[i]
            _, best = self._snapshots[i]
            improvement = (best - current) / best
            if improvement > 0.01:
                self._snapshots[i] = (epoch, current)
                updated = True

        if updated:
            self._epochs_since_update = 0
        else:
            self._epochs_since_update += 1
        return self._epochs_since_update > self._max_epochs_since_update

    def predict_batch_t(self, inputs, batch_size=1024, factored=True):
        """predict batch next state"""
        inputs = self.scaler.transform(inputs)

        ensemble_mean, ensemble_var = [], []
        for i in range(0, inputs.shape[0], batch_size):
            model_input = inputs[i : min(i + batch_size, inputs.shape[0])].float().to(self.device)

            b_mean, b_var = self.ensemble_model(model_input, ret_log_var=False)
            ensemble_mean.append(b_mean)
            ensemble_var.append(b_var)
        ensemble_mean = torch.cat(ensemble_mean, dim=1)
        ensemble_var = torch.cat(ensemble_var, dim=1)

        if factored:
            return ensemble_mean, ensemble_var

        assert False, 'Need to transform to numpy'
        mean = torch.mean(ensemble_mean, dim=0)
        var = torch.mean(ensemble_var, dim=0) + torch.mean(
            torch.square(ensemble_mean - mean[None, :, :]), dim=0
        )
        return mean, var

    def predict_t(self, inputs, batch_size=1024, factored=True):
        inputs = self.scaler.transform(inputs)
        ensemble_mean, ensemble_var = [], []
        for i in range(0, inputs.shape[0], batch_size):
            model_input = inputs[i : min(i + batch_size, inputs.shape[0])].float().to(self.device)
            b_mean, b_var = self.ensemble_model(
                model_input[None, :, :].repeat([self.network_size, 1, 1]), ret_log_var=False
            )
            ensemble_mean.append(b_mean)
            ensemble_var.append(b_var)
        ensemble_mean = torch.cat(ensemble_mean, dim=1)
        ensemble_var = torch.cat(ensemble_var, dim=1)

        if factored:
            return ensemble_mean, ensemble_var

        assert False, 'Need to transform to numpy'
        mean = torch.mean(ensemble_mean, dim=0)
        var = torch.mean(ensemble_var, dim=0) + torch.mean(
            torch.square(ensemble_mean - mean[None, :, :]), dim=0
        )
        return mean, var

    def predict(self, inputs, batch_size=1024, factored=True):
        inputs = self.scaler.transform(inputs)
        ensemble_mean, ensemble_var = [], []
        for i in range(0, inputs.shape[0], batch_size):
            model_input = (
                torch.from_numpy(inputs[i : min(i + batch_size, inputs.shape[0])])
                .float()
                .to(self.device)
            )
            b_mean, b_var = self.ensemble_model(
                model_input[None, :, :].repeat([self.network_size, 1, 1]), ret_log_var=False
            )
            ensemble_mean.append(b_mean.detach().cpu().numpy())
            ensemble_var.append(b_var.detach().cpu().numpy())
        ensemble_mean = np.hstack(ensemble_mean)
        ensemble_var = np.hstack(ensemble_var)
        if factored:
            return ensemble_mean, ensemble_var

        assert False, 'Need to transform to numpy'
        mean = torch.mean(ensemble_mean, dim=0)
        var = torch.mean(ensemble_var, dim=0) + torch.mean(
            torch.square(ensemble_mean - mean[None, :, :]), dim=0
        )
        return mean, var


class Swish(nn.Module):
    """Transform data using sigmoid function."""

    def __init__(self):
        super(Swish, self).__init__()

    def forward(self, input_data):
        """sigmoid function"""
        output = input_data * F.sigmoid(input_data)
        return output