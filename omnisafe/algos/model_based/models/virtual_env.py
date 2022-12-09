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
"""Virtual Environment"""

import numpy as np
import torch


class VirtualEnv:
    """Virtual environment for generating data or planning"""

    def __init__(self, algo, model, env_name, device=torch.device('cpu')):
        self.algo = algo
        self.model = model
        self.env_name = env_name
        self.device = device

    def _termination_fn(self, env_name, obs, act, next_obs):
        """Terminal function"""
        if env_name == 'Hopper-v2':  # pylint: disable=no-else-return
            assert len(obs.shape) == len(next_obs.shape) == len(act.shape) == 2

            height = next_obs[:, 0]
            angle = next_obs[:, 1]
            not_done = (
                np.isfinite(next_obs).all(axis=-1)
                * np.abs(next_obs[:, 1:] < 100).all(axis=-1)
                * (height > 0.7)
                * (np.abs(angle) < 0.2)
            )

            done = ~not_done
            done = done[:, None]
            return done
        elif env_name == 'Walker2d-v2':
            assert len(obs.shape) == len(next_obs.shape) == len(act.shape) == 2

            height = next_obs[:, 0]
            angle = next_obs[:, 1]
            not_done = (height > 0.8) * (height < 2.0) * (angle > -1.0) * (angle < 1.0)
            done = ~not_done
            done = done[:, None]
            return done
        elif 'walker_' in env_name:
            torso_height = next_obs[:, -2]
            torso_ang = next_obs[:, -1]
            if 'walker_7' in env_name or 'walker_5' in env_name:
                offset = 0.0
            else:
                offset = 0.26
            not_done = (
                (torso_height > 0.8 - offset)
                * (torso_height < 2.0 - offset)
                * (torso_ang > -1.0)
                * (torso_ang < 1.0)
            )
            done = ~not_done
            done = done[:, None]
            return done
        else:
            return False

    def _get_logprob(self, input_data, means, variances):

        k = input_data.shape[-1]
        log_prob = (
            -1
            / 2
            * (
                k * np.log(2 * np.pi)
                + np.log(variances).sum(-1)
                + (np.power(input_data - means, 2) / variances).sum(-1)
            )
        )

        # [ batch_size ]
        prob = np.exp(log_prob).sum(0)

        # [ batch_size ]
        log_prob = np.log(prob)

        stds = np.std(means, 0).mean(-1)

        return log_prob, stds

    def step(self, obs, act, deterministic=False):  # pylint:disable=too-many-locals
        """Predict next state"""
        if len(obs.shape) == 1:
            obs = obs[None]
            act = act[None]
            return_single = True
        else:
            return_single = False

        inputs = np.concatenate((obs, act), axis=-1)
        ensemble_model_means, ensemble_model_vars = self.model.predict(inputs)
        if self.algo == 'safe-loop':
            ensemble_model_means[:, :, 1:] += obs

        ensemble_model_stds = np.sqrt(ensemble_model_vars)

        if deterministic:
            ensemble_samples = ensemble_model_means
        else:
            ensemble_samples = (
                ensemble_model_means
                + np.random.normal(size=ensemble_model_means.shape) * ensemble_model_stds
            )

        _, batch_size, _ = ensemble_model_means.shape
        model_idxes = np.random.choice(self.model.elite_model_idxes, size=batch_size)
        batch_idxes = np.arange(0, batch_size)

        samples = ensemble_samples[model_idxes, batch_idxes]
        model_means = ensemble_model_means[model_idxes, batch_idxes]
        model_stds = ensemble_model_stds[model_idxes, batch_idxes]

        log_prob, dev = self._get_logprob(samples, ensemble_model_means, ensemble_model_vars)

        if self.algo == 'safe-loop':
            rewards, next_obs = samples[:, :1], samples[:, 1:]
        elif self.algo == 'mbppo-lag':
            next_obs_delta = samples
            next_obs = next_obs_delta + obs
        terminals = self._termination_fn(self.env_name, obs, act, next_obs)
        if self.algo == 'safe-loop':
            batch_size = model_means.shape[0]
            return_means = np.concatenate(
                (model_means[:, :1], terminals, model_means[:, 1:]), axis=-1
            )
            return_stds = np.concatenate(
                (model_stds[:, :1], np.zeros((batch_size, 1)), model_stds[:, 1:]), axis=-1
            )

        if return_single:
            next_obs = next_obs[0]
            if self.algo == 'safe-loop':
                return_means = return_means[0]
                return_stds = return_stds[0]
                rewards = rewards[0]
                terminals = terminals[0]
        if self.algo == 'safe-loop':
            info = {'mean': return_means, 'std': return_stds, 'log_prob': log_prob, 'dev': dev}
            return next_obs, rewards, terminals, info
        return next_obs

    def step_elite(self, obs, act, idx, deterministic=False):  # pylint:disable=too-many-locals
        """Predict next state"""
        if len(obs.shape) == 1:
            obs = obs[None]
            act = act[None]
            return_single = True
        else:
            return_single = False

        inputs = np.concatenate((obs, act), axis=-1)
        ensemble_model_means, ensemble_model_vars = self.model.predict(inputs)
        ensemble_model_stds = np.sqrt(ensemble_model_vars)

        if deterministic:
            ensemble_samples = ensemble_model_means
        else:
            ensemble_samples = (
                ensemble_model_means
                + np.random.normal(size=ensemble_model_means.shape) * ensemble_model_stds
            )

        _, batch_size, _ = ensemble_model_means.shape
        model_idxes = np.random.choice([idx], size=batch_size)
        batch_idxes = np.arange(0, batch_size)
        samples = ensemble_samples[model_idxes, batch_idxes]
        next_obs_delta = samples
        next_obs = next_obs_delta + obs

        if return_single:
            next_obs = next_obs[0]

        return next_obs

    def get_forward_prediction_random_ensemble_t(self, obs, act, deterministic=False):
        """Randomly predict next state"""
        if len(obs.shape) == 1:
            obs = obs[None]
            act = act[None]

        inputs = torch.cat((obs, act), dim=-1)
        ensemble_model_means, ensemble_model_vars = self.model.predict_t(inputs)

        ensemble_model_means[:, :, 1:] += obs
        ensemble_model_stds = torch.sqrt(ensemble_model_vars)

        if deterministic:
            ensemble_samples = ensemble_model_means
        else:
            ensemble_samples = (
                ensemble_model_means
                + torch.randn(size=ensemble_model_means.shape).to(self.device) * ensemble_model_stds
            )

        _, batch_size, _ = ensemble_model_means.shape
        model_idxes = np.random.choice(self.model.elite_model_idxes, size=batch_size)
        batch_idxes = np.arange(0, batch_size)

        samples = ensemble_samples[model_idxes, batch_idxes]

        return samples

    def get_forward_prediction_random_ensemble(self, obs, act, deterministic=False):
        """Predict next state"""
        if len(obs.shape) == 1:
            obs = obs[None]
            act = act[None]

        if torch.is_tensor(obs):
            obs = obs.detach().cpu().numpy()
        if torch.is_tensor(act):
            act = act.detach().cpu().numpy()
        inputs = np.concatenate((obs, act), axis=-1)
        ensemble_model_means, ensemble_model_vars = self.model.predict(inputs)

        ensemble_model_means[:, :, 1:] += obs
        ensemble_model_stds = np.sqrt(ensemble_model_vars)

        if deterministic:
            ensemble_samples = ensemble_model_means
        else:
            ensemble_samples = (
                ensemble_model_means
                + np.random.normal(size=ensemble_model_means.shape) * ensemble_model_stds
            )

        _, batch_size, _ = ensemble_model_means.shape
        model_idxes = np.random.choice(self.model.elite_model_idxes, size=batch_size)
        batch_idxes = np.arange(0, batch_size)

        samples = ensemble_samples[model_idxes, batch_idxes]

        return samples

    def get_forward_prediction(self, obs, act, deterministic=False):
        """Predict next state"""
        if len(obs.shape) == 1:
            obs = obs[None]
            act = act[None]

        if torch.is_tensor(obs):
            obs = obs.detach().cpu().numpy()
        if torch.is_tensor(act):
            act = act.detach().cpu().numpy()

        inputs = np.concatenate((obs, act), axis=-1)
        ensemble_model_means, ensemble_model_vars = self.model.predict(inputs)

        ensemble_model_means[:, :, 1:] += obs
        ensemble_model_stds = np.sqrt(ensemble_model_vars)

        if deterministic:
            ensemble_samples = ensemble_model_means
        else:
            ensemble_samples = (
                ensemble_model_means
                + np.random.normal(size=ensemble_model_means.shape) * ensemble_model_stds
            )

        samples = ensemble_samples[self.model.elite_model_idxes, :]
        return samples

    def get_forward_prediction_t(self, obs, act, deterministic=False):
        """Predict next state"""
        if len(obs.shape) == 1:
            obs = obs[None]
            act = act[None]

        inputs = torch.cat((obs, act), dim=-1)
        ensemble_model_means, ensemble_model_vars = self.model.predict_batch_t(inputs)

        ensemble_model_means[:, :, 1:] += obs
        ensemble_model_stds = torch.sqrt(ensemble_model_vars)

        if deterministic:
            ensemble_samples = ensemble_model_means
        else:
            ensemble_samples = (
                ensemble_model_means
                + torch.randn(size=ensemble_model_means.shape).to(self.device) * ensemble_model_stds
            )
        return ensemble_samples