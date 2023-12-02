"""Computes RAG retrieval and response results."""


from typing import Any, Sequence, Union

from pydantic import BaseModel

from ..data.dataset import Column, Dataset, Filter, SemanticSearch
from ..gen.generator_openai import OpenAIChatCompletionGenerator
from ..schema import ROWID, SPAN_KEY, Item, Path, normalize_path


# NOTE: I know this is unused. Going to use this for the router soon, so want to keep it for that
# PR.
class RagRetrievalConfig(BaseModel):
  """The config for the rag retrieval."""

  embedding: str

  # The main column that will be used for the retrieval. This should have an embedding index already
  # computed.
  path: Path
  # Columns that will be used for additional metadata information.
  metadata_columns: Sequence[Union[Column, Path]] = []
  filters: Sequence[Filter] = []

  # Hyper-parameters.
  chunk_window: int = 1
  top_k: int = 10
  similarity_threshold: float = 0.0


class RagRetrievalResultItem(BaseModel):
  """The result of the RAG retrieval model."""

  rowid: str
  text: str
  metadata: dict[str, Any]

  # The spans inside the text that matched the original query.
  match_spans: list[tuple[int, int]]


class RagRetrievalResult(BaseModel):
  """The result of the RAG retrieval model."""

  results: list[RagRetrievalResultItem]


class RagGeneratorConfig(BaseModel):
  """The request for the select rows endpoint."""

  prompt_template: str


class RagConfig(BaseModel):
  """The config for the RAG."""

  retrieval: RagRetrievalConfig
  generator: RagGeneratorConfig


def get_rag_retrieval(
  query: str,
  dataset: Dataset,
  embedding: str,
  path: Path,
  metadata_columns: Sequence[Union[Column, Path]] = [],
  filters: Sequence[Filter] = [],
  chunk_window: int = 1,
  top_k: int = 5,
  similarity_threshold: float = 0.0,
) -> list[RagRetrievalResultItem]:
  """Get the retrieval from RAG."""
  # Make sure the dataset has the embedding at the field path.
  manifest = dataset.manifest()
  path = normalize_path(path)
  if not manifest.data_schema.has_field((*path, embedding)):
    raise ValueError(
      f'Embedding {embedding} not found at path {path}. Please compute an embedding before '
      f'running the RAG retrieval. To compute an embedding, please run: '
      f'`dataset.compute_embedding("{embedding}", path)'
    )

  cols: Sequence[Union[Column, Path]] = [ROWID, path, *metadata_columns]
  searches = [
    SemanticSearch(
      path=path,
      embedding=embedding,
      query=query,
      query_type='question',
    )
  ]

  res = dataset.select_rows(columns=cols, filters=filters, limit=top_k, searches=searches)
  select_rows_schema = dataset.select_rows_schema(
    columns=cols, searches=searches, combine_columns=True
  ).data_schema

  # Get the similarity key from the schema.
  semantic_similarity_key = ''
  for sim_path, field in select_rows_schema.all_fields:
    if field.signal and field.signal['signal_name'] == 'semantic_similarity':
      semantic_similarity_key = '.'.join(sim_path)
      break

  # Flatten all of the similarity spans. Similarity results are a tuple of (rowid, spanid, span).
  similarity_results: list[tuple[str, int, Item]] = []
  for row in res:
    similarity_results.extend(
      [(row[ROWID], i, r) for i, r in enumerate(row[semantic_similarity_key])]
    )

  # Sort results by score.
  similarity_results.sort(key=lambda x: x[2]['score'], reverse=True)

  # Choose the topk results.
  similarity_results = similarity_results[:top_k]

  retrieval_results: list[RagRetrievalResultItem] = []
  for rowid, span_id, span in similarity_results:
    # Find the row in the `res` array by rowid key.
    row = next(r for r in res if r[ROWID] == rowid)

    # Get the window around the span_id.
    start_span_id = max(0, span_id - chunk_window)
    end_span_id = min(len(row[semantic_similarity_key]), span_id + chunk_window + 1)
    start_span_start = row[semantic_similarity_key][start_span_id][SPAN_KEY]['start']
    end_span_end = row[semantic_similarity_key][end_span_id - 1][SPAN_KEY]['end']

    retrieval_results.append(
      RagRetrievalResultItem(
        rowid=rowid,
        text=row['.'.join(path)][start_span_start:end_span_end],
        metadata={},
        # Offset the span given by the start and end span start and end.
        match_spans=[
          (span[SPAN_KEY]['start'] - start_span_start, span[SPAN_KEY]['end'] - start_span_start)
        ],
      )
    )

  return retrieval_results


QUERY_TEMPLATE_VAR = '{query_str}'
CONTEXT_TEMPLATE_VAR = '{context_str}'
DEFAULT_PROMPT_TEMPLATE = f"""
`Context information is below.
---------------------
{CONTEXT_TEMPLATE_VAR}
---------------------
Given the context information and not prior knowledge, answer the query.
Query: {QUERY_TEMPLATE_VAR}
Answer: \
`,
"""


def get_rag_response(
  query: str,
  retrieval_results: list[RagRetrievalResultItem],
  prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
) -> str:
  """Get the response from RAG."""
  context = '\n'.join([result.text for result in retrieval_results])
  prompt = prompt_template.replace(QUERY_TEMPLATE_VAR, query).replace(CONTEXT_TEMPLATE_VAR, context)
  return OpenAIChatCompletionGenerator(
    response_description='The answer to the question, given the context and query.'
  ).generate(prompt)