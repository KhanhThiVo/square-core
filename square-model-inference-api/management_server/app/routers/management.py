import logging
import os
from typing import List

import requests
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.models.management import (
    DeployRequest,
    GetModelsHealth,
    GetModelsResult,
    TaskGenericModel,
    TaskResultModel,
    UpdateModel,
)
from app.routers import client_credentials, utils
from docker_access import get_all_model_prefixes
from mongo_access import MongoClass
from tasks.tasks import deploy_task, remove_model_task


logger = logging.getLogger(__name__)
router = APIRouter()

mongo_client = MongoClass()


@router.get("/deployed-models", name="get-deployed-models", response_model=List[GetModelsResult])
async def get_all_models():  # token: str = Depends(client_credentials)):
    """
    Get all the models deployed on the platform in list format
    """
    models = await mongo_client.get_models_db()
    result = []
    for m in models:
        result.append(
            GetModelsResult(
                identifier=m["IDENTIFIER"],
                model_type=m["MODEL_TYPE"],
                model_name=m["MODEL_NAME"],
                disable_gpu=m["DISABLE_GPU"],
                batch_size=m["BATCH_SIZE"],
                max_input=m["MAX_INPUT_SIZE"],
                model_class=m["MODEL_CLASS"],
                return_plaintext_arrays=m["RETURN_PLAINTEXT_ARRAYS"],
            )
        )
    return result


@router.get(
    "/deployed-models-health",
    response_model=List[GetModelsHealth],
    name="get-deployed-models-health",
)
async def get_models_health(token: str = Depends(client_credentials)):
    models = await mongo_client.get_models_db()
    lst_models = []
    for m in models:
        r = requests.get(
            url="{}/api/{}/health/heartbeat".format(settings.API_URL, m["IDENTIFIER"]),
            headers={"Authorization": f"Bearer {token}"},
            verify=os.getenv("VERIFY_SSL", 1) == 1,
        )
        # if the model-api instance has not finished loading the model it is not available yet
        if r.status_code == 200:
            lst_models.append({"identifier": m["IDENTIFIER"], "is_alive": r.json()["is_alive"]})
        else:
            lst_models.append({"identifier": m["IDENTIFIER"], "is_alive": False})

    return lst_models


@router.post("/deploy", name="deploy-model", response_model=TaskGenericModel)
async def deploy_new_model(request: Request, model_params: DeployRequest):
    """
    deploy a new model to the platform
    """
    user_id = await utils.get_user_id(request)
    logger.info(user_id)
    env = {
        "IDENTIFIER": model_params.identifier,
        "MODEL_NAME": model_params.model_name,
        "MODEL_PATH": model_params.model_path,
        "DECODER_PATH": model_params.decoder_path,
        "MODEL_TYPE": model_params.model_type,
        "MODEL_CLASS": model_params.model_class,
        "DISABLE_GPU": model_params.disable_gpu,
        "BATCH_SIZE": model_params.batch_size,
        "MAX_INPUT_SIZE": model_params.max_input,
        "TRANSFORMERS_CACHE": model_params.transformers_cache,
        "RETURN_PLAINTEXT_ARRAYS": model_params.return_plaintext_arrays,
        "PRELOADED_ADAPTERS": model_params.preloaded_adapters,
        "WEB_CONCURRENCY": os.getenv("WEB_CONCURRENCY", 1),  # fixed processes, do not give the control to  end-user
        "KEYCLOAK_BASE_URL": os.getenv("KEYCLOAK_BASE_URL", "https://square.ukp-lab.de"),
    }

    identifier_new = await (mongo_client.check_identifier_new(env["IDENTIFIER"]))
    if not identifier_new:
        raise HTTPException(status_code=406, detail="A model with that identifier already exists")
    res = deploy_task.delay(user_id, env)
    logger.info(res.id)
    return {"message": f"Queued deploying {env['IDENTIFIER']}", "task_id": res.id}


@router.delete("/remove/{identifier}", name="remove-model", response_model=TaskGenericModel)
async def remove_model(request: Request, identifier):
    """
    Remove a model from the platform
    """
    # check if the model is deployed
    logger.info(identifier)
    check_model_id = await (mongo_client.check_identifier_new(identifier))
    if check_model_id:
        raise HTTPException(status_code=406, detail="A model with the input identifier does not exist")
    # check if the user deployed this model
    if await mongo_client.check_user_id(request, identifier):
        res = remove_model_task.delay(identifier)
    else:
        raise HTTPException(status_code=403, detail="Cannot remove a model deployed by another user.")
    return {"message": "Queued removing model.", "task_id": res.id}


@router.patch("/update/{identifier}")
async def update_model(
    request: Request,
    identifier: str,
    update_parameters: UpdateModel,
    token: str = Depends(client_credentials),
):

    check_model_id = await (mongo_client.check_identifier_new(identifier))
    if check_model_id:
        raise HTTPException(status_code=406, detail="A model with the input identifier does not exist")
    if await mongo_client.check_user_id(request, identifier):
        await (mongo_client.update_model_db(identifier, update_parameters))
        logger.info(
            "Update parameters Type {},dict  {}".format(type(update_parameters.dict()), update_parameters.dict())
        )
        r = requests.post(
            url="{}/api/{}/update".format(settings.API_URL, identifier),
            json=update_parameters.dict(),
            headers={"Authorization": f"Bearer {token}"},
            verify=os.getenv("VERIFY_SSL", 1) == 1,
        )
    else:
        raise HTTPException(status_code=403, detail="Cannot update a model deployed by another user")
    return {"status_code": r.status_code, "content": r.json()}


@router.get("/task/{task_id}", name="task-status", response_model=TaskResultModel)
async def get_task_status(task_id):
    """
    Get results from a celery task
    """
    task = AsyncResult(task_id)
    if not task.ready():
        return JSONResponse(status_code=202, content={"task_id": str(task_id), "status": "Processing"})
    result = task.get()
    return {"task_id": str(task_id), "status": "Finished", "result": result}


@router.put("/db/update")
async def init_db_from_docker(token: str = Depends(client_credentials)):
    lst_prefix, lst_container_ids, port = get_all_model_prefixes()
    lst_models = []

    for prefix, container in zip(lst_prefix, lst_container_ids):
        r = requests.get(
            url="{}{}/stats".format(settings.API_URL, prefix),
            headers={"Authorization": f"Bearer {token}"},
            verify=os.getenv("VERIFY_SSL", 1) == 1,
        )
        # if the model-api instance has not finished loading the model it is not available yet
        if r.status_code == 200:
            data = r.json()
            logger.info("Response Format {}".format(data))
            lst_models.append(
                {
                    "USER_ID": "ukp",
                    "IDENTIFIER": prefix.split("/")[-1],
                    "MODEL_NAME": data["model_name"],
                    "MODEL_TYPE": data["model_type"],
                    "DISABLE_GPU": data["disable_gpu"],
                    "BATCH_SIZE": data["batch_size"],
                    "MAX_INPUT_SIZE": data["max_input"],
                    "MODEL_CLASS": data["model_class"],
                    "RETURN_PLAINTEXT_ARRAYS": data["return_plaintext_arrays"],
                    "TRANSFORMERS_CACHE": data.get("transformers_cache", ""),
                    "MODEL_PATH": data.get("model_path", ""),
                    "DECODER_PATH": data.get("decoder_path", ""),
                    "container": container,
                }
            )
        else:
            logger.info("Error retrieving Model Statistics: {}".format(r.json()))
    added_models = await (mongo_client.init_db(lst_models))
    return {"added": added_models}


@router.post("/db/deploy")
async def start_from_db(token: str = Depends(client_credentials)):
    configs = await mongo_client.get_models_db()
    deployed = []
    tasks = []
    for model in configs:
        r = requests.get(
            url="{}/api/{}/health/heartbeat".format(settings.API_URL, model["IDENTIFIER"]),
            headers={"Authorization": f"Bearer {token}"},
            verify=os.getenv("VERIFY_SSL", 1) == 1,
        )
        # if the model-api instance has not finished loading the model it is not available yet
        if r.status_code != 200:
            identifier = model["IDENTIFIER"]
            env = model
            del env["IDENTIFIER"]
            del env["_id"]
            del env["container"]
            res = deploy_task.delay(identifier, env, allow_overwrite=True)
            logger.info(res.id)
            deployed.append(identifier)
            tasks.append(res.id)

    return {"message": f"Queued deploying {deployed}", "task_id": tasks}
