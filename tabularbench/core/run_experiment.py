from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from tabularbench.config.config_run import ConfigRun
from tabularbench.core.dataset_split import make_dataset_split
from tabularbench.core.enums import DatasetSize, DataSplit, ModelName, Task
from tabularbench.core.get_model import get_model
from tabularbench.core.get_trainer import get_trainer
from tabularbench.data.dataset_openml import OpenMLDataset
from tabularbench.results.run_metrics import RunMetrics
from tabularbench.utils.debugger import debugger_is_active
from tabularbench.utils.paths_and_filenames import CONFIG_RUN_FILE_NAME
from tabularbench.utils.set_seed import set_seed


def run_experiment(cfg: ConfigRun) -> Optional[RunMetrics]:

    cfg.save(cfg.output_dir / CONFIG_RUN_FILE_NAME)

    logger.info(f"Start experiment on {cfg.openml_dataset_name} (id={cfg.openml_dataset_id}) with {cfg.model_name.value} doing {cfg.task.value}")

    set_seed(cfg.seed)
    logger.info(f"Set seed to {cfg.seed}")

    logger.info(f"We are using the following hyperparameters:")
    for key, value in cfg.hyperparams.items():
        logger.info(f"    {key}: {value}")

    if debugger_is_active():
        metrics = run_experiment_(cfg)
    else:
        try:
            metrics = run_experiment_(cfg)
        except Exception as e:
            logger.exception("Exception occurred while running experiment")        
            return None
    
    logger.info(f"Finished experiment on {cfg.openml_dataset_name} (id={cfg.openml_dataset_id}) with {cfg.model_name} doing {cfg.task.name}")
    logger.info(f"Final scores: ")

    for i in range(metrics.ds.sizes['cv_split']):
        logger.info((
            f"cv_split_{i} :: "
            f"train: {metrics.ds['score'].sel(data_split=DataSplit.TRAIN.value, cv_split=i):.4f}, "
            f"val: {metrics.ds['score'].sel(data_split=DataSplit.VALID.value, cv_split=i):.4f}, "
            f"test: {metrics.ds['score'].sel(data_split=DataSplit.TEST.value, cv_split=i):.4f}"
        ))

    logger.info((
        f"cv_average :: "
        f"train: {metrics.ds['score'].sel(data_split=DataSplit.TRAIN.value).mean():.4f}, "
        f"val: {metrics.ds['score'].sel(data_split=DataSplit.VALID.value).mean():.4f}, "
        f"test: {metrics.ds['score'].sel(data_split=DataSplit.TEST.value).mean():.4f}"
    ))

    if metrics is not None:
        metrics.save(cfg.output_dir / "metrics.nc")

    return metrics


def run_experiment_(cfg: ConfigRun) -> RunMetrics:

    dataset = OpenMLDataset(cfg.datafile_path, cfg.task)
    metrics = RunMetrics()

    for split_i, (x_train, x_val, x_test, y_train, y_val, y_test, categorical_indicator) in enumerate(dataset.split_iterator()):

        logger.info(f"Start split {split_i+1}/{dataset.n_splits} of {cfg.openml_dataset_name} (id={cfg.openml_dataset_id}) with {cfg.model_name.name} doing {cfg.task.name}")

        data = Data.from_standard_datasplits(x_train, x_val, x_test, y_train, y_val, y_test, cfg.task)

        model = get_model(cfg, data.x_train_cut, data.y_train_cut, categorical_indicator)
        trainer = get_trainer(cfg, model, dataset.n_classes)
        trainer.train(data.x_train_cut, data.y_train_cut, data.x_val_earlystop, data.y_val_earlystop)

        prediction_metrics_train = trainer.test(data.x_train, data.y_train, data.x_train, data.y_train)
        prediction_metrics_val = trainer.test(data.x_train, data.y_train, data.x_val_hyperparams, data.y_val_hyperparams)
        prediction_metrics_test = trainer.test(data.x_train_and_val, data.y_train_and_val, data.x_test, data.y_test)

        logger.info(f"split_{split_i} :: train: {prediction_metrics_train.score:.4f}, val: {prediction_metrics_val.score:.4f}, test: {prediction_metrics_test.score:.4f}")

        metrics.append(prediction_metrics_train, prediction_metrics_val, prediction_metrics_test)

    metrics.post_process()
    return metrics


@dataclass
class Data():
    x_train: np.ndarray
    x_train_cut: np.ndarray
    x_train_and_val: np.ndarray
    x_val_earlystop: np.ndarray
    x_val_hyperparams: np.ndarray
    x_test: np.ndarray
    y_train: np.ndarray
    y_train_cut: np.ndarray
    y_train_and_val: np.ndarray
    y_val_earlystop: np.ndarray
    y_val_hyperparams: np.ndarray
    y_test: np.ndarray


    @classmethod
    def from_standard_datasplits(cls, x_train, x_val, x_test, y_train, y_val, y_test, task: Task):
        x_train_cut, x_val_earlystop, y_train_cut, y_val_earlystop = make_dataset_split(x_train, y_train, task=task)
        x_train_and_val = np.concatenate([x_train_cut, x_val_earlystop], axis=0)
        y_train_and_val = np.concatenate([y_train_cut, y_val_earlystop], axis=0)

        return cls(
            x_train=x_train,
            x_train_cut=x_train_cut,
            x_train_and_val=x_train_and_val,
            x_val_earlystop=x_val_earlystop,
            x_val_hyperparams=x_val,
            x_test=x_test,
            y_train=y_train,
            y_train_cut=y_train_cut,
            y_train_and_val=y_train_and_val,
            y_val_earlystop=y_val_earlystop,
            y_val_hyperparams=y_val,
            y_test=y_test
        )



if __name__ == "__main__":

    import torch

    cfg = ConfigRun(
        output_dir = Path("output_run_experiment"),
        device = torch.device("cuda:6"),
        model_name = ModelName.FOUNDATION,
        seed = 0,
        task = Task.CLASSIFICATION,
        dataset_size = DatasetSize.MEDIUM,
        openml_dataset_id = 44156,
        openml_dataset_name = "electricity",
        datafile_path = Path("data/datasets/whytrees_44156_MEDIUM.nc"),
        hyperparams = dict({
            'n_features': 100,
            'n_classes': 10,
            'dim': 512,
            'n_layers': 12,
            'n_heads': 4,
            'attn_dropout': 0.0,
            'y_as_float_embedding': True,
            'max_samples_support': 8192,
            'max_samples_query': 512,
            'max_epochs': 3,
            'optimizer': 'adamw',
            'lr': 1.e-5,
            'weight_decay': 0,
            'lr_scheduler': False,
            'lr_scheduler_patience': 30,
            'early_stopping_patience': 40,
            'use_pretrained_weights': True,
            'path_to_weights': Path("outputs_done/foundation_forest_big_300k/weights/model_step_300000.pt"),
            'n_ensembles': 1,
            'use_quantile_transformer': True,
            'use_feature_count_scaling': True
        })
    )

    run_experiment(cfg)
