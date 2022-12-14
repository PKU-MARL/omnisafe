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
"""Implementation of the TD3 algorithm."""

import torch

from omnisafe.algorithms import registry
from omnisafe.algorithms.off_policy.ddpg import DDPG


@registry.register
class TD3(DDPG):  # pylint: disable=too-many-instance-attributes
    """The Twin Delayed DDPG (TD3) algorithm.

    References:
        Title: Addressing Function Approximation Error in Actor-Critic Methods
        Authors: Scott Fujimoto, Herke van Hoof, David Meger.
        URL: https://arxiv.org/abs/1802.09477
    """

    def __init__(self, env_id: str, cfgs=None) -> None:
        """Initialize DDPG."""
        super().__init__(
            env_id=env_id,
            cfgs=cfgs,
        )

    def compute_loss_v(self, data):
        """Computing value loss.

        Args:
            data (dict): data from replay buffer.

        Returns:
            torch.Tensor.
        """
        obs, act, rew, obs_next, done = (
            data['obs'],
            data['act'],
            data['rew'],
            data['obs_next'],
            data['done'],
        )
        q_value_list = self.actor_critic.critic(obs, act)
        # Bellman backup for Q function
        with torch.no_grad():
            act_targ = self.ac_targ.actor.predict(obs, deterministic=False, need_log_prob=False)
            q_targ = torch.min(torch.vstack(self.ac_targ.critic(obs_next, act_targ)), dim=0).values
            backup = rew + self.cfgs.gamma * (1 - done) * q_targ
        # MSE loss against Bellman backup
        loss_q = []
        q_values = []
        for q_value in q_value_list:
            loss_q.append(torch.mean((q_value - backup) ** 2))
            q_values.append(torch.mean(q_value))

        # Useful info for logging
        q_info = dict(QVals=sum(q_values).detach().numpy())
        return sum(loss_q), q_info
