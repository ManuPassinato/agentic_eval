import sys

import pyarrow as pa
import pyarrow.parquet as pq
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentic_eval.environments.aneel.build import build_corpus


async def test_stdio_mcp_lists_and_calls_corpus_tools(tmp_path):
    raw_data = tmp_path / "raw" / "data"
    raw_data.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "bucket_name": "test",
                    "blob_path": "legislacao/ren20211000.pdf",
                    "format": "pdf",
                    "text": "RESOLUÇÃO NORMATIVA Nº 1.000, DE 2021\nRegras de distribuição.",
                    "idx": 0,
                    "origin": "Legislação ANEEL",
                    "categories": ["Outros"],
                    "category_paths": ["Outros"],
                    "family": "Resolução Normativa",
                    "prefix": "ren",
                    "role": "Ato principal",
                    "issuer": "aneel",
                }
            ]
        ),
        raw_data / "train-00000-of-00003.parquet",
    )
    database = tmp_path / "aneel.sqlite3"
    build_corpus(tmp_path / "raw", database)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "agentic_eval.environments.aneel.mcp_server",
            "--db",
            str(database),
        ],
    )

    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool(
            "get_resolucao_normativa",
            {
                "number": "1000",
                "year": 2021,
            },
        )

    names = {tool.name for tool in tools.tools}
    assert len(names) == 90
    assert {
        "search_despacho",
        "get_resolucao_normativa",
        "list_portaria",
        "search_manual",
        "get_aneel_document",
        "get_aneel_corpus_stats",
        "finish",
    } <= names
    assert {
        "search_aneel_documents",
        "get_aneel_act",
        "list_aneel_documents",
    }.isdisjoint(names)
    assert '"status": "found"' in str(result.content)
    assert "aneel-" in str(result.content)

    selected_parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "agentic_eval.environments.aneel.mcp_server",
            "--db",
            str(database),
            "--family",
            "resolucao_normativa",
        ],
    )
    async with (
        stdio_client(selected_parameters) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        selected_tools = await session.list_tools()

    assert {tool.name for tool in selected_tools.tools} == {
        "search_resolucao_normativa",
        "get_resolucao_normativa",
        "list_resolucao_normativa",
        "get_aneel_document",
        "get_aneel_corpus_stats",
        "finish",
    }
