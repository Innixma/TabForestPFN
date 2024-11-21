from __future__ import annotations

import logging

import einops
import numpy as np
import torch
from sklearn.base import BaseEstimator

from autogluon.core.metrics import Scorer

from tabularbench.config.config_run import ConfigRun
from tabularbench.core.callbacks import Checkpoint, EarlyStopping, TrackOutput
from tabularbench.core.collator import CollatorWithPadding
from tabularbench.core.enums import Task
from tabularbench.core.get_loss import get_loss
from tabularbench.core.get_optimizer import get_optimizer
from tabularbench.core.get_scheduler import get_scheduler
from tabularbench.core.y_transformer import create_y_transformer
from tabularbench.data.dataset_finetune import DatasetFinetune, DatasetFinetuneGenerator
from tabularbench.data.preprocessor import Preprocessor
from tabularbench.results.prediction_metrics import PredictionMetrics

logger = logging.getLogger(__name__)


class TrainerFinetune(BaseEstimator):

    def __init__(
        self,
        cfg: ConfigRun,
        model: torch.nn.Module,
        n_classes: int,
        stopping_metric: Scorer,
        use_best_epoch: bool = True,
    ) -> None:
        self.cfg = cfg
        self.model = model
        self.model.to(self.cfg.device)
        self.n_classes = n_classes
        
        self.loss = get_loss(self.cfg.task)
        self.optimizer = get_optimizer(self.cfg.hyperparams, self.model)
        self.scheduler = get_scheduler(self.cfg.hyperparams, self.optimizer)
        self.use_best_epoch = use_best_epoch

        self.early_stopping = EarlyStopping(patience=self.cfg.hyperparams['early_stopping_patience'])
        self.checkpoint = Checkpoint(save_best=self.use_best_epoch, in_memory=True)
        self.preprocessor = Preprocessor( 
            use_quantile_transformer=self.cfg.hyperparams['use_quantile_transformer'],
            use_feature_count_scaling=self.cfg.hyperparams['use_feature_count_scaling'],
            max_features=self.cfg.hyperparams['n_features'],
        )

        self.stopping_metric = stopping_metric

    def train(self, x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray, y_val: np.ndarray):
        # FIXME: Figure out best way to seed model
        torch.manual_seed(0)
        np.random.seed(0)
        random_state = np.random.default_rng(seed=0)

        self.preprocessor.fit(x_train, y_train)       
        x_train = self.preprocessor.transform(x_train) 
        x_val = self.preprocessor.transform(x_val)
        self.y_transformer = create_y_transformer(y_train, self.cfg.task)
        
        dataset_train_generator = DatasetFinetuneGenerator(
            self.cfg,
            x = x_train,
            y = self.y_transformer.transform(y_train),
            task = self.cfg.task,
            max_samples_support = self.cfg.hyperparams['max_samples_support'],
            max_samples_query = self.cfg.hyperparams['max_samples_query'],
            split = 0.8,
            random_state=random_state,
        )

        dataset_valid = DatasetFinetune(
            self.cfg,
            x_support = x_train, 
            y_support = self.y_transformer.transform(y_train), 
            x_query = x_val,
            y_query = y_val,
            max_samples_support = self.cfg.hyperparams['max_samples_support'],
            max_samples_query = self.cfg.hyperparams['max_samples_query'],
        )

        loader_valid = self.make_loader(dataset_valid, training=False)

        if self.use_best_epoch:
            self.checkpoint.reset()

        max_epochs = self.cfg.hyperparams['max_epochs']

        if max_epochs != 0:
            metrics_valid = self.test_epoch(loader_valid, y_val)
            logger.info(f"Epoch 000 | Train loss: -.---- | Train score: -.---- | Val loss: {metrics_valid.loss:.4f} | Val score: {metrics_valid.score:.4f}")
            if self.use_best_epoch:
                self.checkpoint(self.model, metrics_valid.loss)

        for epoch in range(1, max_epochs+1):

            dataset_train = next(dataset_train_generator)            
            loader_train = self.make_loader(dataset_train, training=True)
            
            metrics_train = self.train_epoch(loader_train)
            metrics_valid = self.test_epoch(loader_valid, y_val)

            logger.info((
                f"Epoch {epoch:03d} "
                f"| Train error: {metrics_train.loss:.4f} | Train score: {metrics_train.score:.4f} "
                f"| Val error: {metrics_valid.loss:.4f} | Val score: {metrics_valid.score:.4f}"
            ))
            if self.use_best_epoch:
                self.checkpoint(self.model, metrics_valid.loss)
            
            self.early_stopping(metrics_valid.loss)
            if self.early_stopping.we_should_stop():
                logger.info("Early stopping")
                break
            self.scheduler.step(metrics_valid.loss)

        if self.use_best_epoch and self.checkpoint.best_model is not None:
            self.model.load_state_dict(self.checkpoint.load())

    def train_epoch(self, dataloader: torch.utils.data.DataLoader) -> PredictionMetrics:

        self.model.train()
        
        output_tracker = TrackOutput()

        for batch in dataloader:
        
            x_support = batch['x_support'].to(self.cfg.device)
            y_support = batch['y_support'].to(self.cfg.device)
            x_query = batch['x_query'].to(self.cfg.device)
            y_query = batch['y_query'].to(self.cfg.device)
            
            y_hat = self.model(x_support, y_support, x_query)

            match self.cfg.task:
                case Task.REGRESSION:
                    y_hat = y_hat[0, :, 0]
                case Task.CLASSIFICATION:
                    y_hat = y_hat[0, :, :self.n_classes]

            y_query = y_query[0, :]

            loss = self.loss(y_hat, y_query)
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            output_tracker.update(einops.asnumpy(y_query), einops.asnumpy(y_hat))

        y_true, y_pred = output_tracker.get()
        y_pred = self.y_transformer.inverse_transform(y_pred)
        prediction_metrics = PredictionMetrics.from_prediction(y_pred, y_true, self.cfg.task, metric=self.stopping_metric)
        return prediction_metrics

    def test_epoch(self, dataloader: torch.utils.data.DataLoader, y_test: np.ndarray) -> PredictionMetrics:
        # FIXME: test_epoch might be better if it uses the for loop logic with n_ensembles
        y_hat = self.predict_epoch(dataloader)
        y_hat_finish = self.y_transformer.inverse_transform(y_hat)

        prediction_metrics = PredictionMetrics.from_prediction(y_hat_finish, y_test, self.cfg.task, metric=self.stopping_metric)
        return prediction_metrics

    def predict(self, x_support: np.ndarray, y_support: np.ndarray, x_query: np.ndarray) -> np.ndarray:
        """
        Give a prediction for the query set.
        """

        x_support = self.preprocessor.transform(x_support)
        x_query = self.preprocessor.transform(x_query)

        dataset = DatasetFinetune(
            self.cfg, 
            x_support = x_support, 
            y_support = y_support, 
            x_query = x_query,
            y_query = None,
            max_samples_support = self.cfg.hyperparams['max_samples_support'],
            max_samples_query = self.cfg.hyperparams['max_samples_query'],
        )

        loader = self.make_loader(dataset, training=False)

        y_hat_ensembles = []

        for _ in range(self.cfg.hyperparams['n_ensembles']):
            y_hat = self.predict_epoch(loader)
            y_hat_ensembles.append(y_hat)

        y_hat_ensembled = sum(y_hat_ensembles) / self.cfg.hyperparams['n_ensembles']
        y_hat_finish = self.y_transformer.inverse_transform(y_hat_ensembled)

        return y_hat_finish

    def predict_epoch(self, dataloader: torch.utils.data.DataLoader) -> np.ndarray:
        """
        Returns the predictions for the data in the dataloader.
        The predictions are in the original state as they come from the model, i.e. not transformed.
        """

        self.model.eval()

        y_hat_list = []

        with torch.no_grad():
            for batch in dataloader:
                
                x_support = batch['x_support'].to(self.cfg.device)
                y_support = batch['y_support'].to(self.cfg.device)
                x_query = batch['x_query'].to(self.cfg.device)
                
                y_hat = self.model(x_support, y_support, x_query)

                match self.cfg.task:
                    case Task.REGRESSION:
                        y_hat = y_hat[0, :, 0]
                    case Task.CLASSIFICATION:
                        y_hat = y_hat[0, :, :self.n_classes]

                y_hat_list.append(einops.asnumpy(y_hat))

        y_hat = np.concatenate(y_hat_list, axis=0)
        return y_hat

    def load_params(self, path):
        self.model.load_state_dict(torch.load(path))
    

    def make_loader(self, dataset, training):

        return torch.utils.data.DataLoader(
            dataset,
            batch_size=1,
            shuffle=training,
            pin_memory=True,
            num_workers=0,
            drop_last=False,
            collate_fn=CollatorWithPadding(
                pad_to_n_support_samples=None
            )
        )

    