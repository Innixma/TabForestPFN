from __future__ import annotations
from pathlib import Path
import sys
from omegaconf import OmegaConf, DictConfig
import logging

import random
import numpy as np
import torch

from tabularbench.sweeps.paths_and_filenames import CONFIG_DUPLICATE


def get_config(output_dir: str) -> DictConfig:
    return OmegaConf.load(Path(output_dir) / CONFIG_DUPLICATE)  # type: ignore
    

def get_logger(log_path: Path) -> logging.Logger:

    logging.setLogRecordFactory(CustomLogRecord)
    logger = logging.getLogger()
    
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s :: %(levelname)-8s :: %(funcNameMaxWidth)-15s ::   %(message)s')
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


class CustomLogRecord(logging.LogRecord):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.funcNameMaxWidth = self.funcName[:12] + '...' if len(self.funcName) > 15 else self.funcName

    
def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def add_device_and_seed_to_cfg(cfg: DictConfig, gpu: int, seed: int) -> None:
    cfg.device = f'cuda:{gpu}' if torch.cuda.is_available() else 'cpu'
    cfg.seed = seed