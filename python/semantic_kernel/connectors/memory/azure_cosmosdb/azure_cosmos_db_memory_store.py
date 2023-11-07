# Copyright (c) Microsoft. All rights reserved.
import json
from abc import ABC, abstractmethod
from typing import List, Tuple

import numpy as np
from numpy import ndarray
from pymongo.mongo_client import MongoClient

from semantic_kernel.memory.memory_record import MemoryRecord
from semantic_kernel.memory.memory_store_base import MemoryStoreBase
from semantic_kernel.utils.settings import azure_cosmos_db_settings_from_dot_env

# Load environment variables
cosmos_api, cosmos_connstr, cosmos_database_name, cosmos_container_name = azure_cosmos_db_settings_from_dot_env()

# OpenAI Ada Embeddings Dimension
VECTOR_DIMENSION = 1536


# Abstract class similar to the original data store that allows API level abstraction
class AzureCosmosDBStoreApi(ABC):
    @abstractmethod
    async def ensure(self, num_lists, similarity, vector_dimensions):
        raise NotImplementedError

    @abstractmethod
    async def create_collection_core(self, collection_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_collections_core(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    async def delete_collection_core(self, collection_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def does_collection_exist_core(self, collection_name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def upsert_core(self, collection_name: str, record: MemoryRecord) -> str:
        raise NotImplementedError

    @abstractmethod
    async def upsert_batch_core(
        self, collection_name: str, records: List[MemoryRecord]
    ) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    async def get_core(
        self, collection_name: str, key: str, with_embedding: bool
    ) -> MemoryRecord:
        raise NotImplementedError

    @abstractmethod
    async def get_batch_core(
        self, collection_name: str, keys: List[str], with_embeddings: bool
    ) -> List[MemoryRecord]:
        raise NotImplementedError

    @abstractmethod
    async def remove_core(self, collection_name: str, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def remove_batch_core(self, collection_name: str, keys: List[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_nearest_matches_core(
        self,
        collection_name: str,
        embedding: ndarray,
        limit: int,
        min_relevance_score: float,
        with_embeddings: bool,
    ) -> List[Tuple[MemoryRecord, float]]:
        raise NotImplementedError

    @abstractmethod
    async def get_nearest_match_core(
        self,
        collection_name: str,
        embedding: ndarray,
        min_relevance_score: float,
        with_embedding: bool,
    ) -> Tuple[MemoryRecord, float]:
        raise NotImplementedError


class MongoStoreApi(AzureCosmosDBStoreApi):
    def __init__(self, mongoClient: MongoClient):
        self.collection = None
        self.mongoClient = mongoClient

    async def ensure(self, num_lists, similarity, vector_dimensions):
        assert self.mongoClient.is_mongos
        self.collection = self.mongoClient[cosmos_database_name][
            cosmos_container_name
        ]

        indexes = self.collection.index_information()
        if indexes.get("embedding_cosmosSearch") is None:
            indexDefs: List[any] = [
                {
                    "name": "embedding_cosmosSearch",
                    "key": {"embedding": "cosmosSearch"},
                    "cosmosSearchOptions": {
                        "kind": "vector-ivf",
                        "numLists": num_lists,
                        "similarity": similarity,
                        "dimensions": vector_dimensions,
                    },
                }
            ]
            self.mongoClient[cosmos_database_name].command(
                "createIndexes", cosmos_container_name, indexes=indexDefs
            )

    async def create_collection_core(self, collection_name: str) -> None:
        pass

    async def get_collections_core(self) -> List[str]:
        return self.mongoClient[cosmos_database_name].list_collection_names()

    async def delete_collection_core(self, collection_name: str) -> None:
        return self.collection.drop()

    async def does_collection_exist_core(self, collection_name: str) -> bool:
        return (
            cosmos_container_name
            in self.mongoClient[cosmos_database_name].list_collection_names()
        )

    async def upsert_core(self, collection_name: str, record: MemoryRecord) -> str:
        result = await self.upsert_batch_core(collection_name, [record])
        return result[0]

    async def upsert_batch_core(
        self, collection_name: str, records: List[MemoryRecord]
    ) -> List[str]:
        doc_ids: List[str] = []
        cosmosRecords: List[dict] = []
        for record in records:
            cosmosRecord: dict = {
                "_id": record.id,
                "embedding": record.embedding.tolist(),
                "text": record.text,
                "description": record.description,
                "metadata": self.__serialize_metadata(record),
            }
            if record.timestamp is not None:
                cosmosRecord["timestamp"] = record.timestamp

            doc_ids.append(cosmosRecord["_id"])
            cosmosRecords.append(cosmosRecord)
        self.collection.insert_many(cosmosRecords)
        return doc_ids

    async def get_core(
        self, collection_name: str, key: str, with_embedding: bool
    ) -> MemoryRecord:
        if not with_embedding:
            result = self.collection.find_one({"_id": key}, {"embedding": 0})
        else:
            result = self.collection.find_one({"_id": key})
        return MemoryRecord.local_record(
            id=result["_id"],
            embedding=np.array(result["embedding"]) if with_embedding else np.array([]),
            text=result["text"],
            description=result["description"],
            additional_metadata=result["metadata"],
            timestamp=result["timestamp"],
        )

    async def get_batch_core(
        self, collection_name: str, keys: List[str], with_embeddings: bool
    ) -> List[MemoryRecord]:
        if not with_embeddings:
            results = self.collection.find({"_id": {"$in": keys}}, {"embedding": 0})
        else:
            results = self.collection.find({"_id": {"$in": keys}})

        return [
            MemoryRecord.local_record(
                id=result["_id"],
                embedding=np.array(result["embedding"])
                if with_embeddings
                else np.array([]),
                text=result["text"],
                description=result["description"],
                additional_metadata=result["metadata"],
                timestamp=result["timestamp"],
            )
            for result in results
        ]

    async def remove_core(self, collection_name: str, key: str) -> None:
        self.collection.delete_one({"_id": key})

    async def remove_batch_core(self, collection_name: str, keys: List[str]) -> None:
        self.collection.delete_many({"_id": {"$in": keys}})

    async def get_nearest_matches_core(
        self,
        collection_name: str,
        embedding: ndarray,
        limit: int,
        min_relevance_score: float,
        with_embeddings: bool,
    ) -> List[Tuple[MemoryRecord, float]]:
        pipeline = [
            {
                "$search": {
                    "cosmosSearch": {
                        "vector": embedding.tolist(),
                        "path": "embedding",
                        "k": limit},
                    "returnStoredSource": True}
            },
            {
                "$project": {
                    "similarityScore": {
                        "$meta": "searchScore"
                    },
                    "document": "$$ROOT"
                }
            }
        ]
        nearest_results = []
        # Perform vector search
        for aggResult in self.collection.aggregate(pipeline):
            result = MemoryRecord.local_record(
                id=aggResult["_id"],
                embedding=np.array(aggResult["document"]["embedding"])
                if with_embeddings
                else np.array([]),
                text=aggResult["document"]["text"],
                description=aggResult["document"]["description"],
                additional_metadata=aggResult["document"]["metadata"],
                timestamp=aggResult["document"]["timestamp"]
            )
            if aggResult["similarityScore"] < min_relevance_score:
                continue
            else:
                nearest_results.append((result, aggResult["similarityScore"]))
        return nearest_results

    async def get_nearest_match_core(
        self,
        collection_name: str,
        embedding: ndarray,
        min_relevance_score: float,
        with_embedding: bool,
    ) -> Tuple[MemoryRecord, float]:
        nearest_results = await self.get_nearest_matches_core(
            collection_name=collection_name,
            embedding=embedding,
            min_relevance_score=min_relevance_score,
            with_embeddings=with_embedding,
            limit=1,
        )

        if len(nearest_results) > 0:
            return nearest_results[0]
        else:
            return None

    @staticmethod
    def __serialize_metadata(record: MemoryRecord) -> str:
        return json.dumps(
            {
                "text": record.text,
                "description": record.description,
                "additional_metadata": record.additional_metadata,
            }
        )


class AzureCosmosDBMemoryStore(MemoryStoreBase):
    """A memory store that uses AzureCosmosDB for MongoDB vCore, to perform vector similarity search on a fully
    managed MongoDB compatible database service.
    https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/vcore/vector-search"""

    # Right now this only supports Mongo, but set up to support more later.
    apiStore: AzureCosmosDBStoreApi = None

    def __init__(self, cosmosStore: AzureCosmosDBStoreApi):
        self.cosmosStore = cosmosStore

    @staticmethod
    async def create(num_lists, similarity, vector_dimensions) -> MemoryStoreBase:
        """Creates the underlying data store based on the API definition"""
        # Right now this only supports Mongo, but set up to support more later.
        apiStore: AzureCosmosDBStoreApi = None
        if cosmos_api == "mongo-vcore":
            mongoClient = MongoClient(cosmos_connstr)
            apiStore = MongoStoreApi(mongoClient)
        else:
            raise NotImplementedError

        await apiStore.ensure(num_lists, similarity, vector_dimensions)
        store = AzureCosmosDBMemoryStore(apiStore)
        return store

    async def create_collection_async(self, collection_name: str) -> None:
        pass

    async def get_collections_async(self) -> List[str]:
        """Gets the list of collections.

        Returns:
            List[str] -- The list of collections.
        """
        return await self.cosmosStore.get_collections_core()

    async def delete_collection_async(self, collection_name: str) -> None:
        """Deletes a collection.

        Arguments:
            collection_name {str} -- The name of the collection to delete.

        Returns:
            None
        """
        return await self.cosmosStore.delete_collection_core(str())

    async def does_collection_exist_async(self, collection_name: str) -> bool:
        """Checks if a collection exists.

        Arguments:
            collection_name {str} -- The name of the collection to check.

        Returns:
            bool -- True if the collection exists; otherwise, False.
        """
        return await self.cosmosStore.does_collection_exist_core(str())

    async def upsert_async(self, collection_name: str, record: MemoryRecord) -> str:
        """Upsert a record.

        Arguments:
            collection_name {str} -- The name of the collection to upsert the record into.
            record {MemoryRecord} -- The record to upsert.

        Returns:
            str -- The unique record id of the record.
        """
        return await self.cosmosStore.upsert_core(str(), record)

    async def upsert_batch_async(
        self, collection_name: str, records: List[MemoryRecord]
    ) -> List[str]:
        """Upsert a batch of records.

        Arguments:
            collection_name {str}        -- The name of the collection to upsert the records into.
            records {List[MemoryRecord]} -- The records to upsert.

        Returns:
            List[str] -- The unique database keys of the records.
        """
        return await self.cosmosStore.upsert_batch_core(str(), records)

    async def get_async(
        self, collection_name: str, key: str, with_embedding: bool
    ) -> MemoryRecord:
        """Gets a record.

        Arguments:
            collection_name {str} -- The name of the collection to get the record from.
            key {str}             -- The unique database key of the record.
            with_embedding {bool} -- Whether to include the embedding in the result. (default: {False})

        Returns:
            MemoryRecord -- The record.
        """
        return await self.cosmosStore.get_core(str(), key, with_embedding)

    async def get_batch_async(
        self, collection_name: str, keys: List[str], with_embeddings: bool
    ) -> List[MemoryRecord]:
        """Gets a batch of records.

        Arguments:
            collection_name {str}  -- The name of the collection to get the records from.
            keys {List[str]}       -- The unique database keys of the records.
            with_embeddings {bool} -- Whether to include the embeddings in the results. (default: {False})

        Returns:
            List[MemoryRecord] -- The records.
        """
        return await self.cosmosStore.get_batch_core(str(), keys, with_embeddings)

    async def remove_async(self, collection_name: str, key: str) -> None:
        """Removes a record.

        Arguments:
            collection_name {str} -- The name of the collection to remove the record from.
            key {str}             -- The unique database key of the record to remove.

        Returns:
            None
        """
        return await self.cosmosStore.remove_core(str(), key)

    async def remove_batch_async(self, collection_name: str, keys: List[str]) -> None:
        """Removes a batch of records.

        Arguments:
            collection_name {str} -- The name of the collection to remove the records from.
            keys {List[str]}      -- The unique database keys of the records to remove.

        Returns:
            None
        """
        return await self.cosmosStore.remove_batch_core(str(), keys)

    async def get_nearest_matches_async(
        self,
        collection_name: str,
        embedding: ndarray,
        limit: int,
        min_relevance_score: float,
        with_embeddings: bool,
    ) -> List[Tuple[MemoryRecord, float]]:
        """Gets the nearest matches to an embedding using vector configuration.

        Parameters:
            collection_name (str)       -- The name of the collection to get the nearest matches from.
            embedding (ndarray)         -- The embedding to find the nearest matches to.
            limit {int}                 -- The maximum number of matches to return.
            min_relevance_score {float} -- The minimum relevance score of the matches. (default: {0.0})
            with_embeddings {bool}      -- Whether to include the embeddings in the results. (default: {False})

        Returns:
            List[Tuple[MemoryRecord, float]] -- The records and their relevance scores.
        """
        return await self.cosmosStore.get_nearest_matches_core(
            str(), embedding, limit, min_relevance_score, with_embeddings
        )

    async def get_nearest_match_async(
        self,
        collection_name: str,
        embedding: ndarray,
        min_relevance_score: float,
        with_embedding: bool,
    ) -> Tuple[MemoryRecord, float]:
        """Gets the nearest match to an embedding using vector configuration parameters.

        Arguments:
            collection_name {str}       -- The name of the collection to get the nearest match from.
            embedding {ndarray}         -- The embedding to find the nearest match to.
            min_relevance_score {float} -- The minimum relevance score of the match. (default: {0.0})
            with_embedding {bool}       -- Whether to include the embedding in the result. (default: {False})

        Returns:
            Tuple[MemoryRecord, float] -- The record and the relevance score.
        """
        return await self.cosmosStore.get_nearest_match_core(
            str(), embedding, min_relevance_score, with_embedding
        )