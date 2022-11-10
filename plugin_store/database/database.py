from asyncio import Lock
from datetime import datetime
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from asgiref.sync import sync_to_async
from sqlalchemy import or_
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import delete, select

from .models.Artifact import Artifact, PluginTag, Tag
from .models.Version import Version

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Optional, Union


class Database:
    def __init__(self, db_path: "Union[str, Path]"):
        self.db_path = db_path
        self.engine = create_async_engine("sqlite+aiosqlite:///{}".format(self.db_path))
        self.lock = Lock()
        self.maker = sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    @sync_to_async()
    def init(self):
        alembic_cfg = Config("/alembic.ini")
        command.upgrade(alembic_cfg, "head")

    async def prepare_tags(self, session: "AsyncSession", tag_names: list[str]) -> "list[Tag]":
        # nested = await session.begin_nested()
        try:
            statement = select(Tag).where(Tag.tag.in_(tag_names)).order_by(Tag.id)
            tags = list((await session.execute(statement)).scalars())
            existing = [tag.tag for tag in tags]
            for tag_name in tag_names:
                if tag_name not in existing:
                    tag = Tag(tag=tag_name)
                    session.add(tag)
                    tags.append(tag)
        except:
            # await nested.rollback()
            raise
        # await nested.commit()
        return tags

    async def insert_artifact(self, session: "AsyncSession", **kwargs) -> "Artifact":
        nested = await session.begin_nested()
        async with self.lock:
            tags = await self.prepare_tags(session, kwargs["tags"])
            plugin = Artifact(
                name=kwargs["name"],
                author=kwargs["author"],
                description=kwargs["description"],
                tags=tags,
            )
            if "id" in kwargs:
                plugin.id = kwargs["id"]
            try:
                session.add(plugin)
            except:
                await nested.rollback()
            await session.commit()
            return await self.get_plugin_by_id(session, plugin.id)

    async def update_artifact(self, session: "AsyncSession", plugin: "Artifact", **kwargs) -> "Artifact":
        nested = await session.begin_nested()
        async with self.lock:
            if "author" in kwargs:
                plugin.author = kwargs["author"]
            if "description" in kwargs:
                plugin.description = kwargs["description"]
            if "tags" in kwargs:
                plugin.tags = await self.prepare_tags(session, kwargs["tags"])
            try:
                session.add(plugin)
            except:
                await nested.rollback()
                raise
            await session.commit()
        return await self.get_plugin_by_id(session, plugin.id)

    async def insert_version(self, session: "AsyncSession", artifact_id: int, **kwargs) -> "Version":
        version = Version(artifact_id=artifact_id, name=kwargs["name"], hash=kwargs["hash"], added_on=datetime.now())
        async with self.lock:
            session.add(version)
            await session.commit()
        return version

    async def search(self, session: "AsyncSession", name=None, tags=None, limit=50, page=0) -> list["Artifact"]:
        statement = select(Artifact).offset(limit * page)
        if name:
            name_select = select(Artifact).where(Artifact.name.like(f"%{name}%"))
            content = (await session.execute(name_select)).scalars().all()
            if not content:
                return []
            statement = statement.filter(or_(*[(Artifact.id == i.id) for i in content]))
        if tags:
            for tag in tags:
                statement = statement.filter(Artifact.tags.any(tag=tag))
        result = (await session.execute(statement)).scalars().all()
        return result or []

    async def get_plugin_by_name(self, session: "AsyncSession", name: str) -> "Optional[Artifact]":
        statement = select(Artifact).where(Artifact.name == name)
        try:
            return (await session.execute(statement)).scalars().first()
        except NoResultFound:
            return None

    async def get_plugin_by_id(self, session: "AsyncSession", id: int) -> "Optional[Artifact]":
        statement = select(Artifact).where(Artifact.id == id)
        try:
            return (await session.execute(statement)).scalars().first()
        except NoResultFound:
            return None

    async def delete_plugin(self, session: "AsyncSession", id: int):
        await session.execute(delete(PluginTag).where(PluginTag.c.artifact_id == id))
        await session.execute(delete(Version).where(Version.artifact_id == id))
        await session.execute(delete(Artifact).where(Artifact.id == id))
        return await session.commit()
