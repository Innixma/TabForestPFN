from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import os
import time

import pandas as pd
import openml
import random
import numpy as np

from tabularbench.configs.all_model_configs import total_config
from tabularbench.run_experiment import train_model_on_config
from tabularbench.sweeps.random_search_object import WandbSearchObject
from tabularbench.sweeps.sweep_config import SweepConfig, sweep_config_maker

SWEEP_FILE_NAME = 'sweep.csv'
RESULTS_FILE_NAME = 'results.csv'
RESULTS_MODIFIED_FILE_NAME = 'results_modified_for_plotting.csv'
PATH_TO_ALL_BENCH_CSV = 'analyses/results/benchmark_total.csv'


def run_sweeps(output_dir: str, gpu: int, seed: int = 0):
    """
    Run all sweeps in the sweep.csv file in the output_dir.
    If main_process is True, then this process will also make the graphs.
    If main_process is False, then this process will only run the sweeps.
    """

    os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu)
    random.seed(seed)
    np.random.seed(seed)

    sweep_csv = pd.read_csv(Path(output_dir) / SWEEP_FILE_NAME)
    sweep_configs = sweep_config_maker(sweep_csv, output_dir)

    for sweep_config in sweep_configs:
        
        search_sweep(sweep_config, is_random=False)
        if sweep_config.random_search:
            search_sweep(sweep_config, is_random=True)


def search_sweep(sweep: SweepConfig, is_random: bool):
    """Perform one sweep: one row of the sweep.csv file."""
    
    config = total_config[sweep.model][sweep.task]
    search_object = WandbSearchObject(config)
    results_path = sweep.path / RESULTS_FILE_NAME
    datasets_all_ids = openml.study.get_suite(sweep.suite_id).tasks
    runs_per_dataset = sweep.runs_per_dataset if is_random else 1
    
    while True:

        datasets_unfinished = get_unfinished_datasets(datasets_all_ids, results_path, runs_per_dataset)

        if len(datasets_unfinished) == 0:
            break
        
        config_run = create_run_config(sweep, datasets_unfinished, search_object, is_random)
        results = train_model_on_config(config_run)

        if results == -1:
            # This is the error code in case the run crashes
            continue

        save_results(results, results_path)


def get_unfinished_datasets(datasets_all_ids: list[int], results_path: Path, runs_per_dataset: int) -> list[int]:

    if not results_path.exists():
        return datasets_all_ids
    
    results_df = pd.read_csv(results_path)
    datasets_run_count = results_df.groupby('data__keyword').count()['data__categorical'].to_dict()

    datasets_unfinished = []
    for dataset_id in datasets_all_ids:
        if dataset_id not in datasets_run_count:
            datasets_unfinished.append(dataset_id)
        elif datasets_run_count[dataset_id] < runs_per_dataset:
            datasets_unfinished.append(dataset_id)

    return datasets_unfinished


def create_run_config(
    sweep: SweepConfig, 
    datasets_unfinished: list[int], 
    search_object: WandbSearchObject,  
    is_random: bool
) -> dict:

    config_base = make_base_config(sweep)
    config_dataset = draw_dataset_config(datasets_unfinished)
    config_hyperparams = search_object.draw_config(type='random' if is_random else 'default')
    config_hp = {'hp': 'random' if is_random else 'default'}
    config_run = {**config_base, **config_dataset, **config_hyperparams, **config_hp}

    return config_run


def make_base_config(sweep: SweepConfig) -> dict:

    if sweep.dataset_size == "small":
        max_train_samples = 1000
    elif sweep.dataset_size == "medium":
        max_train_samples = 10000
    elif sweep.dataset_size == "large":
        max_train_samples = 50000
    else:
        assert type(sweep.dataset_size) == int
        max_train_samples = sweep.dataset_size

    return {
        "data__categorical": sweep.task == 'classif',
        "data__method_name": "openml_no_transform",
        "data__regression": sweep.task == 'regression',
        "regression": sweep.task == 'regression',
        "n_iter": 'auto',
        "max_train_samples": max_train_samples
    }


def draw_dataset_config(datasets_unfinished: list[int]) -> dict:

    dataset_id = random.choice(datasets_unfinished)
    return {
        "data__keyword": dataset_id
    }
    

def save_results(results: dict, results_path: Path):

    df_new = pd.Series(results).to_frame().T

    if not results_path.exists():
        results_path.parent.mkdir(parents=True, exist_ok=True)
        df_new.to_csv(results_path, mode='w', index=False, header=True)
    else:
        df = pd.read_csv(results_path)
        df = df.append(df_new, ignore_index=True)
        df.to_csv(results_path, mode='w', index=False, header=True)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Run random search sweeps')
    parser.add_argument('--output_dir', type=str, help='Path to sweep output directory')
    parser.add_argument('--seed', type=int, help='Seed')
    parser.add_argument('--main_process', action='store_true', help='Whether this is the main process (makes graphs)')

    args = parser.parse_args()

    run_sweeps(args.output_dir, seed=args.seed, main_process=args.main_process)

