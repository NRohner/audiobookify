//! The text pipeline: normalization, chunking, lexicon substitution and respelling,
//! ported from `src/audiobookify/normalize/text.py`, `lexicon/substitution.py` and
//! `phoneme/respelling.py`.

mod lexicon;
mod normalize;
mod num2words;
mod respelling;

pub use lexicon::apply_lexicon;
pub use normalize::{expand_abbreviations, expand_numbers, normalize_text, split_paragraphs, split_sentences};
pub use respelling::phonemes_to_respelling;
