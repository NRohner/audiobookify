//! A port of the parts of num2words 0.5.14 (`lang="en"`) that `expand_numbers` uses:
//! `num2words(int(raw))` and `num2words(float(raw))`. It follows the Python code step
//! by step (`Num2Word_Base.splitnum`/`clean`/`to_cardinal_float`, `Num2Word_EN.merge`),
//! so it keeps num2words' phrasing: "one hundred and five", "one thousand, nine
//! hundred and eighty-four", "twenty-seven point five" for "27.50".

use std::sync::LazyLock;

use num_bigint::BigUint;
use num_integer::Integer;
use num_traits::{FromPrimitive, One, Zero};

/// num2words' `cards`, largest first: 10^303 ("centillion") down to 10^6, then
/// thousand, hundred, ninety..thirty, twenty..zero.
static CARDS: LazyLock<Vec<(BigUint, String)>> = LazyLock::new(|| {
    let lows = ["non", "oct", "sept", "sext", "quint", "quadr", "tr", "b", "m"];
    let units = ["", "un", "duo", "tre", "quattuor", "quin", "sex", "sept", "octo", "novem"];
    let tens = ["dec", "vigint", "trigint", "quadragint", "quinquagint", "sexagint", "septuagint", "octogint", "nonagint"];
    let mut high: Vec<String> = tens.iter().flat_map(|t| units.iter().map(move |u| format!("{u}{t}"))).collect();
    high.reverse();
    high.extend(lows.iter().map(|l| l.to_string()));
    high.insert(0, "cent".to_string());

    let ten = BigUint::from(10u32);
    let mut cards: Vec<(BigUint, String)> =
        high.iter().zip((6..=303u32).rev().step_by(3)).map(|(word, n)| (ten.pow(n), format!("{word}illion"))).collect();
    let mid = [(1000, "thousand"), (100, "hundred"), (90, "ninety"), (80, "eighty"), (70, "seventy"),
               (60, "sixty"), (50, "fifty"), (40, "forty"), (30, "thirty")];
    cards.extend(mid.iter().map(|(n, w)| (BigUint::from(*n as u32), w.to_string())));
    let low = ["twenty", "nineteen", "eighteen", "seventeen", "sixteen", "fifteen", "fourteen", "thirteen",
               "twelve", "eleven", "ten", "nine", "eight", "seven", "six", "five", "four", "three", "two", "one", "zero"];
    cards.extend(low.iter().zip((0..=20u32).rev()).map(|(w, n)| (BigUint::from(n), w.to_string())));
    cards
});

/// num2words' MAXVAL: 1000 * the largest card. Python raises OverflowError at or above it.
static MAXVAL: LazyLock<BigUint> = LazyLock::new(|| BigUint::from(10u32).pow(306));

#[derive(Clone)]
enum Part {
    Word(String, BigUint),
    Group(Vec<Part>),
}

fn splitnum(value: &BigUint) -> Vec<Part> {
    for (elem, word) in CARDS.iter() {
        if elem > value {
            continue;
        }
        let (div, rem) = if value.is_zero() { (BigUint::one(), BigUint::zero()) } else { value.div_rem(elem) };
        let mut out = Vec::new();
        if div.is_one() {
            out.push(Part::Word("one".to_string(), BigUint::one()));
        } else {
            // ponytail: Python's "tally" branch (div == value) only fires for elem == 1,
            // which English never reaches with div > 1, so it isn't ported.
            out.push(Part::Group(splitnum(&div)));
        }
        out.push(Part::Word(word.clone(), elem.clone()));
        if !rem.is_zero() {
            out.push(Part::Group(splitnum(&rem)));
        }
        return out;
    }
    unreachable!("CARDS ends at zero, which is <= any value")
}

fn merge(left: &Part, right: &Part) -> Part {
    let (Part::Word(ltext, lnum), Part::Word(rtext, rnum)) = (left, right) else {
        unreachable!("clean() only merges two words")
    };
    let hundred = BigUint::from(100u32);
    if lnum.is_one() && *rnum < hundred {
        Part::Word(rtext.clone(), rnum.clone())
    } else if hundred > *lnum && lnum > rnum {
        Part::Word(format!("{ltext}-{rtext}"), lnum + rnum)
    } else if *lnum >= hundred && hundred > *rnum {
        Part::Word(format!("{ltext} and {rtext}"), lnum + rnum)
    } else if rnum > lnum {
        Part::Word(format!("{ltext} {rtext}"), lnum * rnum)
    } else {
        Part::Word(format!("{ltext}, {rtext}"), lnum + rnum)
    }
}

fn clean(mut val: Vec<Part>) -> Part {
    let mut out = val.clone();
    while val.len() != 1 {
        out = Vec::new();
        if let (Part::Word(..), Part::Word(..)) = (&val[0], &val[1]) {
            out.push(merge(&val[0], &val[1]));
            if val.len() > 2 {
                out.push(Part::Group(val[2..].to_vec()));
            }
        } else {
            for elem in &val {
                match elem {
                    Part::Group(group) if group.len() == 1 => out.push(group[0].clone()),
                    Part::Group(group) => out.push(clean(group.clone())),
                    word => out.push(word.clone()),
                }
            }
        }
        val = out.clone();
    }
    out.swap_remove(0)
}

/// `num2words(n)` for a non-negative integer; None where Python raises OverflowError.
fn cardinal(value: &BigUint) -> Option<String> {
    if *value >= *MAXVAL {
        return None;
    }
    match clean(splitnum(value)) {
        Part::Word(text, _) => Some(text),
        Part::Group(_) => unreachable!("clean() reduces to one word"),
    }
}

/// Decimal places in Python's `repr(value)`: `abs(Decimal(str(value)).as_tuple().exponent)`.
/// Rust's `{:e}` prints the same shortest round-trip digits as Python's repr.
fn decimal_places(value: f64) -> usize {
    let formatted = format!("{value:e}");
    let (mantissa, exponent) = formatted.split_once('e').expect("{:e} always has an exponent");
    let digits = mantissa.chars().filter(char::is_ascii_digit).count() as i64;
    (exponent.parse::<i64>().expect("integer exponent") - (digits - 1)).unsigned_abs() as usize
}

/// `Num2Word_Base.to_cardinal_float` for a finite, non-integral, non-negative value.
fn float_words(value: f64) -> Option<String> {
    let pre = value.trunc();
    let precision = decimal_places(value);
    let scale: f64 = format!("1e{precision}").parse().ok()?; // == float(10**precision)
    let mut post = (value - pre).abs() * scale;
    post = if (post.round_ties_even() - post).abs() < 0.01 { post.round_ties_even() } else { post.floor() };
    let digits = BigUint::from_f64(post)?.to_string();
    let digits = format!("{}{digits}", "0".repeat(precision.saturating_sub(digits.len())));
    let mut out = vec![cardinal(&BigUint::from_f64(pre)?)?, "point".to_string()];
    for digit in digits.chars().take(precision) {
        out.push(cardinal(&BigUint::from(digit.to_digit(10)?))?);
    }
    Some(out.join(" "))
}

/// `num2words(float(raw)) if "." in raw else num2words(int(raw))`, for a regex match
/// with its commas already removed. None means "leave the text as it was".
pub fn expand(raw: &str) -> Option<String> {
    // ponytail: Python's int()/float() also read non-ASCII digits ("٣" -> three); no
    // book has had them, so those stay as written.
    if !raw.is_ascii() {
        return None;
    }
    if !raw.contains('.') {
        return cardinal(&raw.parse().ok()?);
    }
    let value: f64 = raw.parse().ok()?;
    if !value.is_finite() {
        return None;
    }
    if value.fract() == 0.0 {
        return cardinal(&BigUint::from_f64(value)?); // "5.0" -> "five", like Python
    }
    float_words(value)
}
