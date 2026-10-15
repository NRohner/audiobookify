//! The text pipeline: normalization, chunking, lexicon substitution and respelling,
//! ported from `src/audiobookify/normalize/text.py`, `lexicon/substitution.py` and
//! `phoneme/respelling.py`.

mod respelling;

pub use respelling::phonemes_to_respelling;
