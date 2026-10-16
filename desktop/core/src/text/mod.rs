//! The text pipeline: normalization, chunking, lexicon substitution,
//! ported from `src/audiobookify/normalize/text.py` and `lexicon/substitution.py`.

mod chunk;
mod lexicon;
mod normalize;
mod num2words;

pub use chunk::{chunk_text, Chunk, EndsWith};
pub use lexicon::apply_lexicon;
pub use normalize::{expand_abbreviations, expand_numbers, normalize_text, split_paragraphs, split_sentences};
