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
"""Implementation of the early terminated algorithm using PPO."""

from omnisafe.algorithms import registry
from omnisafe.algorithms.on_policy.base.ppo import PPO


@registry.register
class PPOEarlyTerminated(PPO):
    """The early terminated algorithm implemented with PPO.

    References:
        Title: Safe Exploration by Solving Early Terminated MDP
        Authors: Hao Sun, Ziping Xu, Meng Fang, Zhenghao Peng, Jiadong Guo, Bo Dai, Bolei Zhou.
        URL: https://arxiv.org/abs/2107.04200
    """

    def __init__(self, env_id, cfgs) -> None:
        """Initialize PPO_Earyly_Terminated."""
        super().__init__(env_id=env_id, cfgs=cfgs)
