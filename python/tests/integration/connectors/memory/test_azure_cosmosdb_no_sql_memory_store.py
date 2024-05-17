# Copyright (c) Microsoft. All rights reserved.
from typing import List

import numpy as np
import pytest
from azure.cosmos import PartitionKey
from azure.cosmos.aio import CosmosClient

from semantic_kernel.memory.memory_record import MemoryRecord
from semantic_kernel.memory.memory_store_base import MemoryStoreBase

try:
    from python.semantic_kernel.connectors.memory.azure_cosmosdb_no_sql.azure_cosmosdb_no_sql_memory_store import (
        AzureCosmosDBNoSQLMemoryStore,
    )

    azure_cosmosdb_no_sql_memory_store_installed = True
except AssertionError:
    azure_cosmosdb_no_sql_memory_store_installed = False

# Host and Key for CosmosDB No SQl
HOST = ""
KEY = ""
cosmos_client = CosmosClient(HOST, KEY)
database_name = "sk_python_db"
container_name = "sk_python_container"
partition_key = PartitionKey(path="/id")
cosmos_container_properties = {"partition_key": partition_key}

pytest_mark = pytest.mark.skipif(
    not azure_cosmosdb_no_sql_memory_store_installed,
    reason="Azure CosmosDB No SQL Memory Store is not installed",
)


async def azure_cosmosdb_no_sql_memory_store() -> MemoryStoreBase:
    store = AzureCosmosDBNoSQLMemoryStore(
        cosmos_client=cosmos_client,
        database_name=database_name,
        partition_key=partition_key,
        vector_embedding_policy=get_vector_embedding_policy("cosine", "float32", 5),
        indexing_policy=get_vector_indexing_policy("flat"),
        cosmos_container_properties=cosmos_container_properties,
    )
    return store


@pytest.mark.asyncio
async def test_create_get_drop_exists_collection():
    store = await azure_cosmosdb_no_sql_memory_store()

    await store.create_collection_async(collection_name=container_name)

    collection_list = await store.get_collections_async()
    assert container_name in collection_list

    await store.delete_collection_async(collection_name=container_name)

    result = await store.does_collection_exist_async(collection_name=container_name)
    assert result is False


@pytest.mark.asyncio
async def test_upsert_and_get_and_remove():
    store = await azure_cosmosdb_no_sql_memory_store()
    await store.create_collection_async(collection_name=container_name)
    record = get_vector_items()[0]

    doc_id = await store.upsert_async(container_name, record)
    assert doc_id == record.id

    result = await store.get_async(container_name, record.id, with_embedding=True)

    assert result is not None
    assert result.id == record.id
    assert all(result._embedding[i] == record._embedding[i] for i in range(len(result._embedding)))
    await store.remove_async(container_name, record.id)


@pytest.mark.asyncio
async def test_upsert_batch_and_get_batch_remove_batch():
    store = await azure_cosmosdb_no_sql_memory_store()
    await store.create_collection_async(collection_name=container_name)
    records = get_vector_items()

    doc_ids = await store.upsert_batch_async(container_name, records)
    assert len(doc_ids) == 3
    assert all(doc_id in [record.id for record in records] for doc_id in doc_ids)

    results = await store.get_batch_async(container_name, [record.id for record in records], with_embeddings=True)

    assert len(results) == 3
    assert all(result["id"] in [record.id for record in records] for result in results)

    await store.remove_batch_async(container_name, [record.id for record in records])


@pytest.mark.asyncio
async def test_get_nearest_match():
    store = await azure_cosmosdb_no_sql_memory_store()
    await store.create_collection_async(collection_name=container_name)
    records = get_vector_items()
    await store.upsert_batch_async(container_name, records)

    test_embedding = get_vector_items()[0].embedding.copy()
    test_embedding[0] = test_embedding[0] + 0.1

    result = await store.get_nearest_match_async(
        container_name, test_embedding, min_relevance_score=0.0, with_embedding=True
    )

    assert result is not None
    assert result[1] > 0.0

    await store.remove_batch_async(container_name, [record.id for record in records])


@pytest.mark.asyncio
async def test_get_nearest_matches():
    store = await azure_cosmosdb_no_sql_memory_store()
    await store.create_collection_async(collection_name=container_name)
    records = get_vector_items()
    await store.upsert_batch_async(container_name, records)

    test_embedding = get_vector_items()[0].embedding.copy()
    test_embedding[0] = test_embedding[0] + 0.1

    result = await store.get_nearest_matches_async(
        container_name, test_embedding, limit=3, min_relevance_score=0.0, with_embeddings=True
    )

    assert result is not None
    assert len(result) == 3
    assert all(result[i][0].id in [record.id for record in records] for i in range(3))

    await store.remove_batch_async(container_name, [record.id for record in records])


def get_vector_indexing_policy(embedding_type):
    return {
        "indexingMode": "consistent",
        "includedPaths": [{"path": "/*"}],
        "vectorIndexes": [{"path": "/embedding", "type": f"{embedding_type}"}],
    }


def get_vector_embedding_policy(distance_function, data_type, dimensions):
    return {
        "vectorEmbeddings": [
            {
                "path": "/embedding",
                "dataType": f"{data_type}",
                "dimensions": dimensions,
                "distanceFunction": f"{distance_function}",
            }
        ]
    }


def create_embedding(non_zero_pos: int) -> np.ndarray:
    # Create a NumPy array with a single non-zero value of dimension 1546
    embedding = np.zeros(5)
    embedding[non_zero_pos - 1] = 1.0
    return embedding


def get_vector_items() -> List[MemoryRecord]:
    records = []
    record = MemoryRecord(
        id="test_id1",
        text="sample text1",
        is_reference=False,
        embedding=create_embedding(non_zero_pos=2),
        description="description",
        additional_metadata="additional metadata",
        external_source_name="external source",
    )
    records.append(record)

    record = MemoryRecord(
        id="test_id2",
        text="sample text2",
        is_reference=False,
        embedding=create_embedding(non_zero_pos=3),
        description="description",
        additional_metadata="additional metadata",
        external_source_name="external source",
    )
    records.append(record)

    record = MemoryRecord(
        id="test_id3",
        text="sample text3",
        is_reference=False,
        embedding=create_embedding(non_zero_pos=4),
        description="description",
        additional_metadata="additional metadata",
        external_source_name="external source",
    )
    records.append(record)
    return records