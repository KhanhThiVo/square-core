import base64
from io import BytesIO

import numpy as np
import requests

from ..models.index import Index
from .config import settings


def _decode_embeddings(encoded_string: str):
    encoded_string = encoded_string.encode()
    arr_binary = base64.decodebytes(encoded_string)
    arr = np.load(BytesIO(arr_binary))
    return arr


def encode_query(query: str, index: Index):
    if index.query_encoder_model is None:
        return None
    if not settings.MODEL_API_URL:
        raise EnvironmentError("Model API not available.")

    request_url = f"{settings.MODEL_API_URL}/{index.query_encoder_model}/embedding"
    data = {
        "input": [query],
        "adapter_name": index.query_encoder_adapter,
    }

    headers = {"Authorization": settings.MODEL_API_KEY}
    response = requests.post(request_url, json=data, headers=headers)
    if response.status_code != 200:
        print(response.json())
        raise EnvironmentError(f"Model API returned {response.status_code}.")
    else:
        embeddings = _decode_embeddings(response.json()["model_outputs"]["embeddings"]).flatten()
        # The vector returned here may be shorter than the stored document vector.
        # In that case, we fill the remaining values with zeros.
        return embeddings.tolist() + [0] * (index.embedding_size - len(embeddings))