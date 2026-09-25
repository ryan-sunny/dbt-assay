-- GENERATED from templates/Lexer.lean.in by scripts/gen_lean_sql.py. Edit the template.
import Sql.Syntax
/-!
# Tokens

The lexical level of the fragment, over code points. Whitespace and comments (`--` to the end
of the line, and `/* */`) are dropped; keywords are recognised case-insensitively; an unquoted
identifier is lowercased; `!=` is `<>`. Every function is structurally recursive on fuel, so the
kernel runs it on a model's own text.
-/
namespace Sql

inductive Tok where
  | kw (s : Str)
  | ident (s : Str)
  | num (s : Str)
  | str (s : Str)
  | sym (s : Str)
  deriving Repr, BEq, Inhabited, DecidableEq

def keywords : List Str :=
  [[83, 69, 76, 69, 67, 84], [68, 73, 83, 84, 73, 78, 67, 84], [70, 82, 79, 77], [87, 72, 69, 82, 69], [71, 82, 79, 85, 80], [66, 89], [79, 82, 68, 69, 82], [65, 83], [74, 79, 73, 78], [73, 78, 78, 69, 82],
   [76, 69, 70, 84], [82, 73, 71, 72, 84], [70, 85, 76, 76], [79, 85, 84, 69, 82], [67, 82, 79, 83, 83], [79, 78], [85, 83, 73, 78, 71], [65, 78, 68], [79, 82], [78, 79, 84], [73, 83],
   [78, 85, 76, 76], [73, 78], [67, 65, 83, 69], [87, 72, 69, 78], [84, 72, 69, 78], [69, 76, 83, 69], [69, 78, 68], [87, 73, 84, 72], [81, 85, 65, 76, 73, 70, 89], [79, 86, 69, 82],
   [80, 65, 82, 84, 73, 84, 73, 79, 78], [67, 65, 83, 84], [84, 82, 85, 69], [70, 65, 76, 83, 69], [65, 83, 67], [68, 69, 83, 67], [72, 65, 86, 73, 78, 71], [76, 73, 77, 73, 84], [66, 69, 84, 87, 69, 69, 78],
   [76, 73, 75, 69], [73, 76, 73, 75, 69], [85, 78, 73, 79, 78], [65, 76, 76], [70, 73, 76, 84, 69, 82]]

def isDigit (c : Nat) : Bool := 48 ≤ c && c ≤ 57
def isUpperC (c : Nat) : Bool := 65 ≤ c && c ≤ 90
def isLowerC (c : Nat) : Bool := 97 ≤ c && c ≤ 122
def isIdStart (c : Nat) : Bool := isUpperC c || isLowerC c || c == 95 || c ≥ 128
def isIdChar (c : Nat) : Bool := isIdStart c || isDigit c || c == 36
def isSpace (c : Nat) : Bool := c == 32 || c == 10 || c == 9 || c == 13

def lowerS : Str → Str
  | [] => []
  | c :: cs => (if isUpperC c then c + 32 else c) :: lowerS cs

def upperS : Str → Str
  | [] => []
  | c :: cs => (if isLowerC c then c - 32 else c) :: upperS cs

def memS (x : Str) : List Str → Bool
  | [] => false
  | y :: ys => x == y || memS x ys

/-- Codes while `p` holds, and the rest. -/
def span (p : Nat → Bool) : Str → Str × Str
  | [] => ([], [])
  | c :: cs => if p c then let (a, b) := span p cs; (c :: a, b) else ([], c :: cs)

/-- The body of a quoted run up to its closing `q` (a doubled `q` is one `q`), and the rest. -/
def quoted (q : Nat) : Nat → Str → Option (Str × Str)
  | 0, _ => none
  | _, [] => none
  | n + 1, c :: cs =>
    if c == q then
      match cs with
      | d :: ds => if d == q then (quoted q n ds).map (fun (a, b) => (q :: a, b)) else some ([], cs)
      | [] => some ([], [])
    else (quoted q n cs).map (fun (a, b) => (c :: a, b))

def skipBlock : Nat → Str → Str
  | 0, cs => cs
  | _, [] => []
  | _ + 1, 42 :: 47 :: cs => cs
  | n + 1, _ :: cs => skipBlock n cs

def skipLine : Str → Str
  | [] => []
  | 10 :: cs => cs
  | _ :: cs => skipLine cs

def twoSyms : List Str := [[60, 62], [33, 61], [60, 61], [62, 61], [124, 124], [58, 58], [45, 62]]
def oneSyms : List Nat := [40, 41, 44, 46, 42, 61, 60, 62, 43, 45, 47, 37, 59, 91, 93]

def lexAux : Nat → Str → Option (List Tok)
  | 0, _ => none
  | _, [] => some []
  | n + 1, c :: cs =>
    if isSpace c then lexAux n cs
    else if c == 45 && cs.head? == some 45 then lexAux n (skipLine cs)
    else if c == 47 && cs.head? == some 42 then lexAux n (skipBlock n (cs.drop 1))
    else if c == 39 then
      match quoted 39 n cs with
      | some (body, rest) => (lexAux n rest).map (Tok.str body :: ·)
      | none => none
    else if c == 34 then
      match quoted 34 n cs with
      | some (body, rest) => (lexAux n rest).map (Tok.ident body :: ·)
      | none => none
    else if isDigit c then
      let (a, rest) := span (fun d => isDigit d || d == 46) (c :: cs)
      (lexAux n rest).map (Tok.num a :: ·)
    else if isIdStart c then
      let (a, rest) := span isIdChar (c :: cs)
      let t := if memS (upperS a) keywords then Tok.kw (upperS a) else Tok.ident (lowerS a)
      (lexAux n rest).map (t :: ·)
    else match cs with
      | d :: ds =>
        if memS [c, d] twoSyms then
          (lexAux n ds).map (Tok.sym (if [c, d] == [33, 61] then [60, 62] else [c, d]) :: ·)
        else if oneSyms.contains c then (lexAux n cs).map (Tok.sym [c] :: ·)
        else none
      | [] => if oneSyms.contains c then some [Tok.sym [c]] else none

/-- The tokens of a text given as code points, or `none` outside the fragment. -/
def lexCodes (cs : Str) : Option (List Tok) := lexAux (cs.length + 1) cs

/-- The same, from a `String` (for the executable; proofs take code points). -/
def lex (s : String) : Option (List Tok) := lexCodes (s.toList.map Char.toNat)

end Sql
