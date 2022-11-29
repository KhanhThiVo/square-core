import logging
from typing import List

from fastapi import APIRouter

from evaluator.models import DataSet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dataset")


@router.get(
    "",
    response_model=List[str],
)
async def get_data_sets():
    """Returns a list of supported data sets."""
    datasets = [dataset.value for dataset in DataSet]

    logger.debug("get_data_sets {datasets}".format(datasets=datasets))
    return datasets