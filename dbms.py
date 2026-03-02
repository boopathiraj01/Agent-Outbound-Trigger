from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError
from typing import Optional, Union
import logging

logger = logging.getLogger("DBManagement")

from dotenv import load_dotenv
import os
load_dotenv()

uri = os.getenv("MONGODB_URI")


class DBManagement:

    def __init__(self, uri: str = uri, db_name: str = "avatar-dev", collection_name: str = "outbound"):
        self.client     = MongoClient(uri)
        self.db         = self.client[db_name]
        self.collection = self.db[collection_name]
        logger.info(f"✅ Connected → {db_name}.{collection_name}")

    # ── Switch collection at runtime ───────────────────────────────────────────
    def use_collection(self, collection_name: str):
        self.collection = self.db[collection_name]
        logger.info(f"🔄 Switched to collection: {collection_name}")


    # ── INSERT ─────────────────────────────────────────────────────────────────
    def add_data(self, data: Union[dict, list]) -> dict:
        """Insert one document (dict) or many documents (list of dicts)."""
        try:
            if isinstance(data, dict):
                result = self.collection.insert_one(data)
                logger.info(f"➕ Inserted one → {result.inserted_id}")
                return {"status": "success", "inserted_id": str(result.inserted_id)}

            elif isinstance(data, list):
                result = self.collection.insert_many(data)
                logger.info(f"➕ Inserted {len(result.inserted_ids)} documents")
                return {"status": "success", "inserted_count": len(result.inserted_ids)}

            else:
                raise ValueError("data must be a dict or list of dicts")

        except PyMongoError as e:
            logger.error(f"❌ Insert failed: {e}")
            return {"status": "error", "message": str(e)}


    # ── FIND ───────────────────────────────────────────────────────────────────
    def get_data(
        self,
        filter: dict = {},
        projection: Optional[dict] = None,
        limit: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "asc"
    ) -> list:
        """
        Fetch documents matching filter.
        - projection : fields to include/exclude  e.g. {"name": 1, "_id": 0}
        - limit      : max docs to return (0 = all)
        - sort_by    : field name to sort on
        - sort_order : "asc" or "desc"
        """
        try:
            order = ASCENDING if sort_order == "asc" else DESCENDING
            cursor = self.collection.find(filter, projection)

            if sort_by:
                cursor = cursor.sort(sort_by, order)
            if limit:
                cursor = cursor.limit(limit)

            results = []
            for doc in cursor:
                doc["_id"] = str(doc["_id"])   # make _id JSON-serialisable
                results.append(doc)

            logger.info(f"🔍 Found {len(results)} document(s)")
            return results

        except PyMongoError as e:
            logger.error(f"❌ Find failed: {e}")
            return []


    def get_one(self, filter: dict, projection: Optional[dict] = None) -> Optional[dict]:
        """Fetch a single document matching filter."""
        try:
            doc = self.collection.find_one(filter, projection)
            if doc:
                doc["_id"] = str(doc["_id"])
            return doc
        except PyMongoError as e:
            logger.error(f"❌ FindOne failed: {e}")
            return None


    # ── UPDATE ─────────────────────────────────────────────────────────────────
    def update_data(
        self,
        filter: dict,
        update: dict,
        many: bool = False,
        upsert: bool = False
    ) -> dict:
        """
        Update document(s) matching filter.
        - update : use MongoDB operators  e.g. {"$set": {"status": "done"}}
        - many   : True → update all matches | False → update first match only
        - upsert : True → insert if no match found
        """
        try:
            # Auto-wrap plain dict in $set for convenience
            if not any(k.startswith("$") for k in update):
                update = {"$set": update}

            if many:
                result = self.collection.update_many(filter, update, upsert=upsert)
                logger.info(f"✏️  Updated {result.modified_count} document(s)")
                return {
                    "status": "success",
                    "matched": result.matched_count,
                    "modified": result.modified_count,
                    "upserted_id": str(result.upserted_id) if result.upserted_id else None
                }
            else:
                result = self.collection.update_one(filter, update, upsert=upsert)
                logger.info(f"✏️  Updated {result.modified_count} document(s)")
                return {
                    "status": "success",
                    "matched": result.matched_count,
                    "modified": result.modified_count,
                    "upserted_id": str(result.upserted_id) if result.upserted_id else None
                }

        except PyMongoError as e:
            logger.error(f"❌ Update failed: {e}")
            return {"status": "error", "message": str(e)}


    # ── DELETE ─────────────────────────────────────────────────────────────────
    def delete_data(self, filter: dict, many: bool = False) -> dict:
        """
        Delete document(s) matching filter.
        - many : True → delete all matches | False → delete first match only
        """
        try:
            if many:
                result = self.collection.delete_many(filter)
            else:
                result = self.collection.delete_one(filter)

            logger.info(f"🗑️  Deleted {result.deleted_count} document(s)")
            return {"status": "success", "deleted_count": result.deleted_count}

        except PyMongoError as e:
            logger.error(f"❌ Delete failed: {e}")
            return {"status": "error", "message": str(e)}


    # ── COUNT ──────────────────────────────────────────────────────────────────
    def count(self, filter: dict = {}) -> int:
        """Return number of documents matching filter."""
        try:
            return self.collection.count_documents(filter)
        except PyMongoError as e:
            logger.error(f"❌ Count failed: {e}")
            return 0


    # ── AGGREGATE ─────────────────────────────────────────────────────────────
    def aggregate(self, pipeline: list) -> list:
        """Run a MongoDB aggregation pipeline."""
        try:
            results = list(self.collection.aggregate(pipeline))
            for doc in results:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
            return results
        except PyMongoError as e:
            logger.error(f"❌ Aggregate failed: {e}")
            return []

    # ── CLOSE ──────────────────────────────────────────────────────────────────
    def close(self):
        self.client.close()
        logger.info("🔌 MongoDB connection closed.")