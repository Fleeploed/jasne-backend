-- =====================================================================
-- Jasně — replace ivfflat with HNSW for knowledge_chunks.embedding.
--
-- The ivfflat index from 0001 was built while the table was empty, so its
-- 100 cluster centroids were trained on nothing — recall degrades as real
-- chunks are loaded and the index never re-trains itself. HNSW needs no
-- training step: it builds incrementally and keeps good recall as the
-- corpus grows, so loading the remaining 9 categories needs no REINDEX.
-- =====================================================================

drop index if exists knowledge_chunks_embedding_idx;

create index knowledge_chunks_embedding_idx
  on knowledge_chunks using hnsw (embedding vector_cosine_ops);
