use crate::pystr;

// Static espeak-IPA-symbol -> ASCII mapping. Deliberately simple: no syllable
// boundaries or stress marking. The derived respelling is a starting point the
// user corrects before it's used, not a phonetically precise transcription.
const IPA_TO_ASCII: &[(&str, &str)] = &[
    ("p", "p"), ("b", "b"), ("t", "t"), ("d", "d"), ("k", "k"), ("ɡ", "g"), ("g", "g"),
    ("tʃ", "ch"), ("dʒ", "j"), ("f", "f"), ("v", "v"), ("θ", "th"), ("ð", "th"),
    ("s", "s"), ("z", "z"), ("ʃ", "sh"), ("ʒ", "zh"), ("h", "h"), ("m", "m"), ("n", "n"),
    ("ŋ", "ng"), ("l", "l"), ("ɹ", "r"), ("r", "r"), ("w", "w"), ("j", "y"),
    ("i", "ee"), ("iː", "ee"), ("ɪ", "ih"), ("ɛ", "eh"), ("e", "eh"), ("æ", "a"),
    ("ɑ", "ah"), ("ɑː", "ah"), ("ɔ", "aw"), ("ɔː", "aw"), ("ʊ", "uu"), ("u", "oo"),
    ("uː", "oo"), ("ʌ", "uh"), ("ə", "uh"), ("ɚ", "er"), ("ɝ", "er"),
    ("eɪ", "ay"), ("aɪ", "eye"), ("ɔɪ", "oy"), ("aʊ", "ow"), ("oʊ", "oh"),
];

pub fn phonemes_to_respelling(phonemes: &str) -> String {
    phonemes
        .split(pystr::is_space)
        .filter(|token| !token.is_empty())
        .map(|token| IPA_TO_ASCII.iter().find(|(ipa, _)| *ipa == token).map_or(token, |(_, ascii)| ascii))
        .collect()
}
