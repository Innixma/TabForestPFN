from enum import IntEnum, StrEnum


class Task(StrEnum):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"


class DatasetSize(IntEnum):
    SMALL = 1000
    MEDIUM = 10000
    LARGE = 50000


class ModelName(StrEnum):
    FOUNDATION = "Foundation"
