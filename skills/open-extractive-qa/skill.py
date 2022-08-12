import logging
from typing import Iterable

from square_skill_api.models import QueryOutput, QueryRequest

from square_skill_helpers import DataAPI, ModelAPI

logger = logging.getLogger(__name__)

model_api = ModelAPI()
data_api = DataAPI()


async def predict(request: QueryRequest) -> QueryOutput:
    """Given a question, performs open-domain, extractive QA. First, background
    knowledge is retrieved using a specified index and retrieval method. Next, the top k
    documents are used for span extraction. Finally, the extracted answers are returned.
    """

    query = request.query
    explain_kwargs = request.explain_kwargs or {}

    context = request.skill_args.get("context")
    if not context:
        data = await data_api(
            datastore_name=request.skill_args["datastore"],
            index_name=request.skill_args.get("index", ""),
            query=query,
        )
        logger.info(f"Data API output:\n{data}")
        context = [d["document"]["text"] for d in data]
        context_score = [d["score"] for d in data]

        prepared_input = [[query, c] for c in context]
    else:
        # skip backgrond knowledge retrieval and use context provided
        prepared_input = [[query, context]]
        context_score = 1
    
    model_request = {
        "input": prepared_input,
        "task_kwargs": {"topk": request.skill_args.get("topk", 5)},
        "explain_kwargs": explain_kwargs,
    }
    if request.skill_args.get("adapter"):
        model_request["adapter_name"] = request.skill_args["adapter"]
    model_api_output = await model_api(
        model_name=request.skill_args["base_model"],
        pipeline="question-answering",
        model_request=model_request,
    )
    logger.info(f"Model API output:\n{model_api_output}")

    return QueryOutput.from_question_answering(
        questions=query, model_api_output=model_api_output, context=context, context_score=context_score
    )
