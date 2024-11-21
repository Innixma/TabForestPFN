from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch

from tabularbench.config.config_save_load_mixin import ConfigSaveLoadMixin
from tabularbench.core.enums import DatasetSize, ModelName, Task


@dataclass
class ConfigRun(ConfigSaveLoadMixin):
    output_dir: Path
    seed: int
    model_name: ModelName
    task: Task
    dataset_size: DatasetSize
    openml_dataset_id: int
    openml_dataset_name: str
    datafile_path: Path
    hyperparams: dict
    device: torch.device = None
