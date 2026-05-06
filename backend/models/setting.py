from sqlmodel import SQLModel, Field


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str
