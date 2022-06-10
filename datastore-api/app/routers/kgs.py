import json
import logging
from typing import Iterable, List, Dict

from fastapi import APIRouter, Depends, HTTPException, Response, status, Request, File, UploadFile
from fastapi.param_functions import Body, Path

from ..core.config import settings
from ..models.document import Document
from ..models.datastore import Datastore, DatastoreRequest
from ..models.stats import DatastoreStats
from ..models.httperror import HTTPError
from ..models.upload import UploadResponse, UploadUrlSet
from .dependencies import get_storage_connector, get_kg_storage_connector, get_mongo_client
from ..core.mongo import MongoClient

from ..core.kgs.connector import KnowledgeGraphConnector
from ..routers.documents import upload_document_file

router = APIRouter(tags=["Knowledge Graphs"])
binding_item_type = 'datastore'

@router.get(
    "",
    summary="Get all knowledge graphs",
    description="Get all knowledge graphs from the datastore API",
    responses={
        200: {
            "model": List[Datastore],
            "description": "List of all knowledge graphs",
        }
    },
    response_model=List[Datastore],
)
async def get_all_kgs(
    conn=Depends(get_kg_storage_connector),
):
    return await conn.get_kgs()


@router.get(
    "/{kg_name}",
    summary="Get a knowledge graph",
    description="Get a knowledge graph by its name from the datastore API",
    responses={
        200: {
            "model": Datastore,
            "description": "The knowledge graph information",
        }
    },
    response_model=Datastore,
)
async def get_kg(
    kg_name: str = Path(..., description="The knowledge graph name"),
    conn=Depends(get_kg_storage_connector),
):
    schema = await conn.get_kg(kg_name)
    if schema is None:
        return Response(status_code=404)
    return schema


###  BUG: PUT-Request goes through and kgs are being created. But is still returning a 500 ERROR
@router.put(
    "/{kg_name}",
    summary="Create a knowledge graph",
    description="Create a new knowledge graph",
    responses={
        200: {
            "model": Datastore,
            "description": "The knowledge graph information",
        },
        400: {
            "description": "Failed to create the knowledge graph in the API database",
        },
        500: {
            "description": "Failed to create the knowledge graph in the storage backend.",
        },
    },
    response_model=Datastore,
)
async def put_kg(
    request: Request,
    kg_name: str = Path(..., description="The knowledge graph name"),
    fields: DatastoreRequest = Body(..., description="The knowledge graph fields"),
    conn: KnowledgeGraphConnector = Depends(get_kg_storage_connector),
    response: Response = None,
    mongo: MongoClient = Depends(get_mongo_client)
):
    # Update if existing, otherwise add new
    schema = await conn.get_datastore(kg_name)
    success = False
    if schema is None:
        # creating a new datastore
        schema = fields.to_datastore(kg_name)
        success = await conn.add_datastore(schema)
        response.status_code = status.HTTP_201_CREATED
        if success:
            await mongo.new_binding(request, kg_name, binding_item_type)  # It should be placed after conn.add_datastore to make sure the status consistent between conn.add_datastore and mongo.new_binding
    else:
        # updating an existing datastore
        await mongo.autonomous_access_checking(request, kg_name, binding_item_type)
        schema = fields.to_datastore(kg_name)
        success = await conn.update_datastore(schema)
        response.status_code = status.HTTP_200_OK

    if success:
        await conn.commit_changes()
        return schema
    else:
        raise HTTPException(status_code=400)


@router.delete(
    "/{kg_name}",
    summary="Delete a knowledge graph",
    description="Delete a knowledge graph from the datastore API",
    responses={
        204: {
            "description": "The knowledge graph is deleted",
        },
        404: {"description": "The knowledge graph could not be deleted from the API database"},
        500: {
            "description": "Failed to delete the knowledge graph from the storage backend.",
        },
    },
)
async def delete_kg(
    request: Request,
    kg_name: str = Path(..., description="The knowledge graph name"),
    conn: KnowledgeGraphConnector = Depends(get_kg_storage_connector),
    mongo: MongoClient = Depends(get_mongo_client)
):
    if not (await conn.get_kg(kg_name)):
        return Response(status_code=404)
    
    await mongo.autonomous_access_checking(request, kg_name, binding_item_type)
    success = await conn.delete_kg(kg_name)

    if success:    
        await mongo.delete_binding(request, kg_name, binding_item_type)
        await conn.commit_changes()
        return Response(status_code=204)
    else:
        return Response(status_code=404)


@router.get(
    "/{kg_name}/stats",
    summary="Get knowledge graph statistics",
    description="Get statistics such as document count and storage size in bytes for a knowledge graph.",
    responses={
        200: {
            "model": DatastoreStats,
            "description": "The knowledge graph statistics",
        },
        404: {"description": "The knowledge graph could not be found"},
    },
    response_model=DatastoreStats,
)
async def get_kg_stats(
    kg_name: str = Path(..., description="The knowledge graph name"),
    conn=Depends(get_kg_storage_connector),
):
    stats = await conn.get_kg_stats(kg_name)
    if stats is not None:
        return stats
    else:
        raise HTTPException(status_code=404)

@router.get(
    "/{kg_name}/relations",
    summary="Get all knowledge graph relations",
    description="Get all relations of a given knowledge graph.",
    responses={
        200: {
            #"model": List[Dict[str,int]],
            "description": "The knowledge graph relations",
        },
        404: {"description": "The knowledge graph relations could not be found"},
    },
    #response_model=List[Dict[str,int]],
)
async def get_kg_relations(
    kg_name: str = Path(..., description="The knowledge graph name"),
    conn=Depends(get_kg_storage_connector),
):
    stats = await conn.get_all_relations(kg_name)
    if stats is not None:
        return stats
    else:
        raise HTTPException(status_code=404)

@router.post(
    "/{kg_name}/nodes",
    summary="Upload a batch of nodes",
    response_model=UploadResponse,
    status_code=201,
    responses={
        200: {"description": "Number of successfully uploaded nodes to the knowledge graph."},
        400: {"model": UploadResponse, "description": "Error during Upload"},
        404: {"model": HTTPError, "description": "The knowledge graph does not exist."},
        422: {"description": "Cannot instantiate a Document object"}
    },
)
async def post_kg_nodes(
    request: Request,
    kg_name: str = Path(..., description="The name of the knowledge graph"),
    documents: List[Document] = Body(..., description="Batch of documents to be uploaded."),
    conn = Depends(get_kg_storage_connector),
    response: Response = None,
    mongo: MongoClient = Depends(get_mongo_client)
):
    kg = await conn.get_kg(kg_name)
    if kg is None:
        raise HTTPException(status_code=404, detail="Knowledge graph not found.")

    await mongo.autonomous_access_checking(request, kg_name, binding_item_type)

    successes, errors = await conn.add_document_batch(kg_name, documents)
    if errors > 0:
        response.status_code = 400
        return UploadResponse(
            message=f"Unable to upload {errors} documents.", successful_uploads=successes, errors=errors
        )
    else:
        return UploadResponse(message=f"Successfully uploaded {successes} documents.", successful_uploads=successes)


@router.get(
    "/{kg_name}/nodes/query_by_name",
    summary="Get a node from a knowledge graph",
    description="Get a node from the knowledge graph by name",
    responses={
        200: {
            "description": "The node",
            "model": Document,
        },
        400: {"model": HTTPError, "description": "Failed to retrieve node"},
    },
)
async def get_node_by_name(
    kg_name: str = Path(..., description="The name of the node"),
    doc_id: str = Path(..., description="The name of the node to retrieve"),
    conn=Depends(get_kg_storage_connector),
):
    result = await conn.get_node(kg_name, doc_id)
    if result is not None:
        return result
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Could not find node.")


@router.post(
    "/{kg_name}/subgraph/query_by_node_name",
    summary="summary",
    description="Get the subgraph of a given List of node-names and the number of hops",
    responses={
        200: {
            #"model": DatastoreStats,
            "description": "The subgraph as a set of nodes and edges",
        },
        404: {"description": "The subgraph could not be retrieved"},
    },
    #response_model=DatastoreStats,
)
async def subgraph_by_names(
    kg_name: str = Path(..., description="The knowledge graph name"),
    nids: set = Body(..., description="List of node names."),
    hops: int = Body(2, description="Number of hops to retrieve."),
    conn=Depends(get_kg_storage_connector),
):

    stats = await conn.extract_subgraph_by_names(kg_name, names=nids, hops=hops)
    if stats is not None:
        return stats
    else:
        raise HTTPException(status_code=404)
