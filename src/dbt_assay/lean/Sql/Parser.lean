-- GENERATED from templates/Parser.lean.in by scripts/gen_lean_sql.py. Edit the template.
import Sql.Lexer
/-!
# The grammar of the fragment

A recursive-descent parser over tokens, with the usual precedence: `OR` < `AND` < `NOT` <
comparison (`= <> < <= > >= LIKE ILIKE`, `IS [NOT] NULL`, `[NOT] IN`, `[NOT] BETWEEN`) <
`+ - ||` < `* / %` < unary minus < `::` cast < primary. Parentheses group and leave no trace in
the tree. Every function recurses structurally on a fuel argument, so the kernel runs it.

This IS the definition of what a text in the fragment means as a tree: a per-model proof shows
that sqlglot's tree for the model is exactly what this parser reads from the model's text.
-/
namespace Sql

abbrev P (α : Type) := List Tok → Option (α × List Tok)

def expectKw (k : Str) : P Unit
  | Tok.kw k' :: ts => if k == k' then some ((), ts) else none
  | _ => none

def expectSym (s : Str) : P Unit
  | Tok.sym s' :: ts => if s == s' then some ((), ts) else none
  | _ => none

def isKw (k : Str) : List Tok → Bool
  | Tok.kw k' :: _ => k == k'
  | _ => false

def isSym (s : Str) : List Tok → Bool
  | Tok.sym s' :: _ => s == s'
  | _ => false

def ident : P Str
  | Tok.ident s :: ts => some (s, ts)
  | _ => none

/-- `a.b.c` -/
def identChain : Nat → P (List Str)
  | 0, _ => none
  | n + 1, ts => do
    let (a, ts) ← ident ts
    if isSym [46] ts && (match ts.drop 1 with | Tok.ident _ :: _ => true | _ => false) then
      let (rest, ts) ← identChain n (ts.drop 1)
      some (a :: rest, ts)
    else some ([a], ts)

/-- One name for each type the engines spell several ways: they are the same type. -/
def normType (t : Str) : Str :=
  match t with
  | [105, 110, 116, 101, 103, 101, 114] | [105, 110, 116, 52] | [115, 105, 103, 110, 101, 100] => [105, 110, 116]
  | [105, 110, 116, 56] | [108, 111, 110, 103] => [98, 105, 103, 105, 110, 116]
  | [105, 110, 116, 50] => [115, 109, 97, 108, 108, 105, 110, 116]
  | [102, 108, 111, 97, 116, 56] | [100, 111, 117, 98, 108, 101, 95, 112, 114, 101, 99, 105, 115, 105, 111, 110] => [100, 111, 117, 98, 108, 101]
  | [102, 108, 111, 97, 116, 52] | [114, 101, 97, 108] => [102, 108, 111, 97, 116]
  | [110, 117, 109, 101, 114, 105, 99] | [110, 117, 109, 98, 101, 114] => [100, 101, 99, 105, 109, 97, 108]
  | [98, 111, 111, 108] | [108, 111, 103, 105, 99, 97, 108] => [98, 111, 111, 108, 101, 97, 110]
  | [115, 116, 114, 105, 110, 103] | [116, 101, 120, 116] | [99, 104, 97, 114, 95, 118, 97, 114, 121, 105, 110, 103] => [118, 97, 114, 99, 104, 97, 114]
  | [100, 97, 116, 101, 116, 105, 109, 101] | [116, 105, 109, 101, 115, 116, 97, 109, 112, 110, 116, 122] => [116, 105, 109, 101, 115, 116, 97, 109, 112]
  | _ => t

/-- One name for each function the engines spell several ways. -/
def normFn (f : Str) : Str :=
  match f with
  | [115, 117, 98, 115, 116, 114] => [115, 117, 98, 115, 116, 114, 105, 110, 103]
  | [105, 102, 110, 117, 108, 108] | [110, 118, 108] => [99, 111, 97, 108, 101, 115, 99, 101]
  | [108, 101, 110] | [99, 104, 97, 114, 95, 108, 101, 110, 103, 116, 104] | [99, 104, 97, 114, 97, 99, 116, 101, 114, 95, 108, 101, 110, 103, 116, 104] => [108, 101, 110, 103, 116, 104]
  | [108, 99, 97, 115, 101] => [108, 111, 119, 101, 114]
  | [117, 99, 97, 115, 101] => [117, 112, 112, 101, 114]
  | [112, 111, 119] => [112, 111, 119, 101, 114]
  | [99, 101, 105, 108, 105, 110, 103] => [99, 101, 105, 108]
  | [100, 97, 121, 95, 111, 102, 95, 119, 101, 101, 107] => [100, 97, 121, 111, 102, 119, 101, 101, 107]
  | [115, 116, 100, 100, 101, 118, 95, 115, 97, 109, 112] => [115, 116, 100, 100, 101, 118]
  | [118, 97, 114, 95, 115, 97, 109, 112] => [118, 97, 114, 105, 97, 110, 99, 101]
  | [97, 114, 114, 97, 121, 95, 97, 103, 103] => [108, 105, 115, 116]
  | [115, 116, 114, 105, 110, 103, 95, 97, 103, 103] | [108, 105, 115, 116, 97, 103, 103] => [103, 114, 111, 117, 112, 95, 99, 111, 110, 99, 97, 116]
  | [100, 97, 116, 101, 100, 105, 102, 102] => [100, 97, 116, 101, 95, 100, 105, 102, 102]
  | [100, 97, 116, 101, 112, 97, 114, 116] => [100, 97, 116, 101, 95, 112, 97, 114, 116]
  | [108, 105, 115, 116, 95, 99, 111, 110, 116, 97, 105, 110, 115] => [97, 114, 114, 97, 121, 95, 99, 111, 110, 116, 97, 105, 110, 115]
  | [115, 116, 114, 105, 110, 103, 95, 115, 112, 108, 105, 116] => [115, 116, 114, 95, 115, 112, 108, 105, 116]
  | _ => f

/-- Functions whose first argument is a date or time unit, written as a string: the unit is
compared case-insensitively, as the engines compare it, so it is kept lowercased. -/
def unitFns : List Str := [[100, 97, 116, 101, 95, 100, 105, 102, 102], [100, 97, 116, 101, 95, 116, 114, 117, 110, 99], [100, 97, 116, 101, 95, 112, 97, 114, 116]]

def lowerUnit (name : Str) : List Expr → List Expr
  | Expr.str u :: rest => if memS name unitFns then Expr.str (lowerS u) :: rest else Expr.str u :: rest
  | xs => xs

/-- Where NULLs sort when an `ORDER BY` key does not say: the dialect's rule, as sqlglot applies
it. `nr` 0: NULLs are small (first ascending); 1: NULLs are large (first descending); 2: NULLs
are always last (DuckDB). -/
def defaultNullsFirst (nr : Nat) (desc : Bool) : Bool :=
  if nr == 0 then !desc else if nr == 1 then desc else false

/-- Identifiers that name a function when written bare, as the engines read them. -/
def bareFns : List Str := [[99, 117, 114, 114, 101, 110, 116, 95, 100, 97, 116, 101], [99, 117, 114, 114, 101, 110, 116, 95, 116, 105, 109, 101, 115, 116, 97, 109, 112], [99, 117, 114, 114, 101, 110, 116, 95, 116, 105, 109, 101]]

/-- Type names that make a typed literal of the string after them: `timestamp '2020-01-01'`. -/
def literalTypes : List Str := [[100, 97, 116, 101], [116, 105, 109, 101, 115, 116, 97, 109, 112], [116, 105, 109, 101], [116, 105, 109, 101, 115, 116, 97, 109, 112, 116, 122]]

/-- A type name: `decimal(18, 2)`, `varchar`, `timestamp`. -/
def typeName : P Str
  | Tok.ident t :: Tok.sym [40] :: Tok.num a :: Tok.sym [44] :: Tok.num b :: Tok.sym [41] :: ts =>
    some (normType t ++ [40] ++ a ++ [44] ++ b ++ [41], ts)
  | Tok.ident t :: Tok.sym [40] :: Tok.num a :: Tok.sym [41] :: ts =>
    some (normType t ++ [40] ++ a ++ [41], ts)
  | Tok.ident t :: ts => some (normType t, ts)
  | _ => none

def cmpOp : List Tok → Option Str
  | Tok.sym s :: _ => if memS s [[61], [60, 62], [60], [60, 61], [62], [62, 61]] then some s else none
  | Tok.kw [76, 73, 75, 69] :: _ => some [76, 73, 75, 69]
  | Tok.kw [73, 76, 73, 75, 69] :: _ => some [73, 76, 73, 75, 69]
  | _ => none

/-- `a, b, c` as names. -/
def pNames : P (List Str)
  | Tok.ident a :: Tok.sym [44] :: ts => (pNames ts).map (fun (xs, ts) => (a :: xs, ts))
  | Tok.ident a :: ts => some ([a], ts)
  | _ => none

section
variable (nr : Nat)

mutual
  def pExpr : Nat → P Expr
    | 0, _ => none
    | n + 1, ts => pOr n ts

  def pOr : Nat → P Expr
    | 0, _ => none
    | n + 1, ts => do
      let (a, ts) ← pAnd n ts
      pOrRest n a ts

  def pOrRest : Nat → Expr → P Expr
    | 0, _, _ => none
    | n + 1, a, ts =>
      if isKw [79, 82] ts then do
        let (b, ts) ← pAnd n (ts.drop 1)
        pOrRest n (Expr.bin [79, 82] a b) ts
      else some (a, ts)

  def pAnd : Nat → P Expr
    | 0, _ => none
    | n + 1, ts => do
      let (a, ts) ← pNot n ts
      pAndRest n a ts

  def pAndRest : Nat → Expr → P Expr
    | 0, _, _ => none
    | n + 1, a, ts =>
      if isKw [65, 78, 68] ts then do
        let (b, ts) ← pNot n (ts.drop 1)
        pAndRest n (Expr.bin [65, 78, 68] a b) ts
      else some (a, ts)

  def pNot : Nat → P Expr
    | 0, _ => none
    | n + 1, ts =>
      if isKw [78, 79, 84] ts then do
        let (a, ts) ← pNot n (ts.drop 1)
        some (Expr.un [78, 79, 84] a, ts)
      else pCmp n ts

  def pCmp : Nat → P Expr
    | 0, _ => none
    | n + 1, ts => do
      let (a, ts) ← pAdd n ts
      match cmpOp ts with
      | some op => do
        let (b, ts) ← pAdd n (ts.drop 1)
        some (Expr.bin op a b, ts)
      | none =>
        if isKw [73, 83] ts then
          let ts := ts.drop 1
          let neg := isKw [78, 79, 84] ts
          let ts := if neg then ts.drop 1 else ts
          if isKw [68, 73, 83, 84, 73, 78, 67, 84] ts then do
            let ((), ts) ← expectKw [70, 82, 79, 77] (ts.drop 1)
            let (b, ts) ← pAdd n ts
            some (Expr.bin (if neg then [73, 83, 32, 78, 79, 84, 32, 68, 73, 83, 84, 73, 78, 67, 84, 32, 70, 82, 79, 77] else [73, 83, 32, 68, 73, 83, 84, 73, 78, 67, 84, 32, 70, 82, 79, 77]) a b, ts)
          else do
            let ((), ts) ← expectKw [78, 85, 76, 76] ts
            some (Expr.isNull a neg, ts)
        else
          let neg := isKw [78, 79, 84] ts
          let ts' := if neg then ts.drop 1 else ts
          if isKw [73, 78] ts' then do
            let ((), ts) ← expectSym [40] (ts'.drop 1)
            let (xs, ts) ← pList n ts
            let ((), ts) ← expectSym [41] ts
            some (Expr.inList a (ExprList.ofList xs) neg, ts)
          else if isKw [66, 69, 84, 87, 69, 69, 78] ts' then do
            let (lo, ts) ← pAdd n (ts'.drop 1)
            let ((), ts) ← expectKw [65, 78, 68] ts
            let (hi, ts) ← pAdd n ts
            some (Expr.between a lo hi neg, ts)
          else if neg && (isKw [76, 73, 75, 69] ts' || isKw [73, 76, 73, 75, 69] ts') then do
            let op := if isKw [76, 73, 75, 69] ts' then [76, 73, 75, 69] else [73, 76, 73, 75, 69]
            let (b, ts) ← pAdd n (ts'.drop 1)
            some (Expr.un [78, 79, 84] (Expr.bin op a b), ts)
          else if neg then none
          else some (a, ts)

  def pAdd : Nat → P Expr
    | 0, _ => none
    | n + 1, ts => do
      let (a, ts) ← pMul n ts
      pAddRest n a ts

  def pAddRest : Nat → Expr → P Expr
    | 0, _, _ => none
    | n + 1, a, ts =>
      match ts with
      | Tok.sym s :: rest =>
        if s == [43] || s == [45] || s == [124, 124] then do
          let (b, ts) ← pMul n rest
          pAddRest n (Expr.bin s a b) ts
        else some (a, ts)
      | _ => some (a, ts)

  def pMul : Nat → P Expr
    | 0, _ => none
    | n + 1, ts => do
      let (a, ts) ← pUnary n ts
      pMulRest n a ts

  def pMulRest : Nat → Expr → P Expr
    | 0, _, _ => none
    | n + 1, a, ts =>
      match ts with
      | Tok.sym s :: rest =>
        if s == [42] || s == [47] || s == [37] then do
          let (b, ts) ← pUnary n rest
          pMulRest n (Expr.bin s a b) ts
        else some (a, ts)
      | _ => some (a, ts)

  def pUnary : Nat → P Expr
    | 0, _ => none
    | n + 1, ts =>
      if isSym [45] ts then do
        let (a, ts) ← pUnary n (ts.drop 1)
        some (Expr.un [45] a, ts)
      else do
        let (a, ts) ← pPrimary n ts
        pCasts n a ts

  def pCasts : Nat → Expr → P Expr
    | 0, _, _ => none
    | n + 1, a, ts =>
      if isSym [58, 58] ts then do
        let (t, ts) ← typeName (ts.drop 1)
        pCasts n (Expr.cast a t) ts
      else if isSym [91] ts then do
        let (i, ts) ← pExpr n (ts.drop 1)
        let ((), ts) ← expectSym [93] ts
        pCasts n (Expr.index a i) ts
      else some (a, ts)

  def pList : Nat → P (List Expr)
    | 0, _ => none
    | n + 1, ts => do
      let (a, ts) ← pExpr n ts
      if isSym [44] ts then do
        let (rest, ts) ← pList n (ts.drop 1)
        some (a :: rest, ts)
      else some ([a], ts)

  def pOrderList : Nat → P (List (Expr × Bool × Bool))
    | 0, _ => none
    | n + 1, ts => do
      let (a, ts) ← pExpr n ts
      let (desc, ts) := if isKw [68, 69, 83, 67] ts then (true, ts.drop 1)
                        else if isKw [65, 83, 67] ts then (false, ts.drop 1) else (false, ts)
      let (nf, ts) := match ts with
        | Tok.ident [110, 117, 108, 108, 115] :: Tok.ident [102, 105, 114, 115, 116] :: ts => (true, ts)
        | Tok.ident [110, 117, 108, 108, 115] :: Tok.ident [108, 97, 115, 116] :: ts => (false, ts)
        | _ => (defaultNullsFirst nr desc, ts)
      if isSym [44] ts then do
        let (rest, ts) ← pOrderList n (ts.drop 1)
        some ((a, desc, nf) :: rest, ts)
      else some ([(a, desc, nf)], ts)

  def pWhens : Nat → P (List (Expr × Expr))
    | 0, _ => none
    | n + 1, ts =>
      if isKw [87, 72, 69, 78] ts then do
        let (c, ts) ← pExpr n (ts.drop 1)
        let ((), ts) ← expectKw [84, 72, 69, 78] ts
        let (v, ts) ← pExpr n ts
        let (rest, ts) ← pWhens n ts
        some ((c, v) :: rest, ts)
      else some ([], ts)

  def pOver : Nat → Expr → P Expr
    | 0, _, _ => none
    | n + 1, f, ts =>
      if (match ts with | Tok.ident [105, 103, 110, 111, 114, 101] :: Tok.ident [110, 117, 108, 108, 115] :: _ => true | _ => false) then
        pOver n (Expr.ignoreNulls f) (ts.drop 2)
      else if isKw [70, 73, 76, 84, 69, 82] ts then do
        let ((), ts) ← expectSym [40] (ts.drop 1)
        let ((), ts) ← expectKw [87, 72, 69, 82, 69] ts
        let (c, ts) ← pExpr n ts
        let ((), ts) ← expectSym [41] ts
        pOver n (Expr.filtered f c) ts
      else if isKw [79, 86, 69, 82] ts then do
        let ((), ts) ← expectSym [40] (ts.drop 1)
        let (part, ts) ← if isKw [80, 65, 82, 84, 73, 84, 73, 79, 78] ts then do
            let ((), ts) ← expectKw [66, 89] (ts.drop 1)
            pList n ts
          else some ([], ts)
        let (ord, ts) ← if isKw [79, 82, 68, 69, 82] ts then do
            let ((), ts) ← expectKw [66, 89] (ts.drop 1)
            pOrderList n ts
          else some ([], ts)
        let ((), ts) ← expectSym [41] ts
        some (Expr.window f (ExprList.ofList part) (OrderList.ofList ord), ts)
      else some (f, ts)

  def pPrimary : Nat → P Expr
    | 0, _ => none
    | n + 1, ts =>
      match ts with
      | Tok.num s :: ts => some (Expr.num s, ts)
      | Tok.str s :: ts => some (Expr.str s, ts)
      | Tok.kw [78, 85, 76, 76] :: ts => some (Expr.null, ts)
      | Tok.kw [84, 82, 85, 69] :: ts => some (Expr.bool true, ts)
      | Tok.kw [70, 65, 76, 83, 69] :: ts => some (Expr.bool false, ts)
      | Tok.sym [42] :: ts => pStarRest n [] ts
      | Tok.sym [40] :: ts => do
        let (a, ts) ← pExpr n ts
        let ((), ts) ← expectSym [41] ts
        some (a, ts)
      | Tok.kw [67, 65, 83, 84] :: ts => do
        let ((), ts) ← expectSym [40] ts
        let (a, ts) ← pExpr n ts
        let ((), ts) ← expectKw [65, 83] ts
        let (t, ts) ← typeName ts
        let ((), ts) ← expectSym [41] ts
        some (Expr.cast a t, ts)
      | Tok.kw [67, 65, 83, 69] :: ts => do
        let (operand, ts) ← if isKw [87, 72, 69, 78] ts then some (none, ts) else do
            let (o, ts) ← pExpr n ts
            some (some o, ts)
        let (whens, ts) ← pWhens n ts
        let (els, ts) ← if isKw [69, 76, 83, 69] ts then do
            let (e, ts) ← pExpr n (ts.drop 1)
            some (some e, ts)
          else some (none, ts)
        let ((), ts) ← expectKw [69, 78, 68] ts
        some (Expr.case (OptExpr.ofOption operand) (WhenList.ofList whens) (OptExpr.ofOption els), ts)
      | Tok.ident [116, 114, 121, 95, 99, 97, 115, 116] :: Tok.sym [40] :: ts => do
        let (a, ts) ← pExpr n ts
        let ((), ts) ← expectKw [65, 83] ts
        let (t, ts) ← typeName ts
        let ((), ts) ← expectSym [41] ts
        some (Expr.tryCast a t, ts)
      | Tok.kw [76, 69, 70, 84] :: Tok.sym [40] :: ts => pCall n [108, 101, 102, 116] ts
      | Tok.kw [82, 73, 71, 72, 84] :: Tok.sym [40] :: ts => pCall n [114, 105, 103, 104, 116] ts
      | Tok.sym [91] :: ts => do
        let (xs, ts) ← if isSym [93] ts then some ([], ts) else pList n ts
        let ((), ts) ← expectSym [93] ts
        some (Expr.list (ExprList.ofList xs), ts)
      | Tok.ident [105, 110, 116, 101, 114, 118, 97, 108] :: Tok.str v :: Tok.ident u :: ts => some (Expr.interval v (lowerS u), ts)
      | Tok.ident [105, 110, 116, 101, 114, 118, 97, 108] :: Tok.num v :: Tok.ident u :: ts => some (Expr.interval v (lowerS u), ts)
      | Tok.ident [105, 110, 116, 101, 114, 118, 97, 108] :: Tok.str v :: ts =>
        match span (fun c => c != 32) v with
        | (count, 32 :: unit) => some (Expr.interval count (lowerS unit), ts)
        | _ => none
      | Tok.ident [101, 120, 116, 114, 97, 99, 116] :: Tok.sym [40] :: Tok.ident u :: Tok.kw [70, 82, 79, 77] :: ts => do
        let (e, ts) ← pExpr n ts
        let ((), ts) ← expectSym [41] ts
        some (Expr.fn [101, 120, 116, 114, 97, 99, 116] false (ExprList.ofList [Expr.str (lowerS u), e]), ts)
      | Tok.ident x :: Tok.sym [45, 62] :: ts => do
        let (b, ts) ← pExpr n ts
        some (Expr.lambda [x] b, ts)
      | Tok.ident t :: Tok.str v :: ts =>
        if memS t literalTypes then some (Expr.cast (Expr.str v) (normType t), ts) else none
      | Tok.ident f :: ts =>
        if memS f bareFns && !isSym [40] ts then some (Expr.fn f false ExprList.nil, ts)
        else pNamed n (Tok.ident f :: ts)
      | _ => none

  /-- A column, `t.*` (with `EXCLUDE`), or a function call named by one identifier. -/
  def pNamed : Nat → P Expr
    | 0, _ => none
    | n + 1, ts =>
      match ts with
      | Tok.ident _ :: _ => do
        let (parts, ts) ← identChain n ts
        if isSym [46] ts && isSym [42] (ts.drop 1) then pStarRest n parts (ts.drop 2)
        else if isSym [40] ts then
          match parts with
          | [name] => pCall n (normFn name) (ts.drop 1)
          | _ => none
        else match parts.reverse with
          | name :: qual => some (Expr.col qual.reverse name, ts)
          | [] => none
      | _ => none

  /-- After `*` or `t.*`: `EXCLUDE (a, b)` or `EXCLUDE a`, or nothing. -/
  def pStarRest : Nat → List Str → P Expr
    | 0, _, _ => none
    | _ + 1, qual, ts =>
      match ts with
      | Tok.ident [101, 120, 99, 108, 117, 100, 101] :: Tok.sym [40] :: ts => do
        let (cs, ts) ← pNames ts
        let ((), ts) ← expectSym [41] ts
        some (Expr.starExcept qual cs, ts)
      | Tok.ident [101, 120, 99, 108, 117, 100, 101] :: Tok.ident c :: ts => some (Expr.starExcept qual [c], ts)
      | _ => some (Expr.star qual, ts)

  /-- A call's arguments after its `(`: `DISTINCT`, the list, `IGNORE NULLS` or `ORDER BY`
  inside, the `)`, then `IGNORE NULLS` / `FILTER` / `OVER` after. -/
  def pCall : Nat → Str → P Expr
    | 0, _, _ => none
    | n + 1, name, ts => do
      let (distinct, ts) := if isKw [68, 73, 83, 84, 73, 78, 67, 84] ts then (true, ts.drop 1) else (false, ts)
      let (args, ts) ← if isSym [41] ts then some ([], ts) else pList n ts
      let args := lowerUnit name args
      let (ign, ts) := match ts with
        | Tok.ident [105, 103, 110, 111, 114, 101] :: Tok.ident [110, 117, 108, 108, 115] :: ts => (true, ts)
        | _ => (false, ts)
      let (ord, ts) ← if isKw [79, 82, 68, 69, 82] ts then do
          let ((), ts) ← expectKw [66, 89] (ts.drop 1)
          pOrderList n ts
        else some ([], ts)
      let ((), ts) ← expectSym [41] ts
      let call := if ord.isEmpty then Expr.fn name distinct (ExprList.ofList args)
                  else Expr.fnOrdered name distinct (ExprList.ofList args) (OrderList.ofList ord)
      pOver n (if ign then Expr.ignoreNulls call else call) ts
end

/-- `select_item [AS alias]`. -/
def pItem (n : Nat) : P (Expr × Option Str) := fun ts => do
  let (e, ts) ← pExpr nr n ts
  if isKw [65, 83] ts then do
    let (a, ts) ← ident (ts.drop 1)
    some ((e, some a), ts)
  else match ts with
    | Tok.ident a :: ts => some ((e, some a), ts)
    | _ => some ((e, none), ts)

def pItems : Nat → P (List (Expr × Option Str))
  | 0, _ => none
  | n + 1, ts => do
    let (a, ts) ← pItem nr n ts
    if isSym [44] ts then do
      let (rest, ts) ← pItems n (ts.drop 1)
      some (a :: rest, ts)
    else some ([a], ts)

def pAlias : P (Option Str)
  | Tok.kw [65, 83] :: Tok.ident a :: ts => some (some a, ts)
  | Tok.ident a :: ts => some (some a, ts)
  | ts => some (none, ts)

def pIdents : Nat → P (List Str)
  | 0, _ => none
  | n + 1, ts => do
    let (a, ts) ← ident ts
    if isSym [44] ts then do
      let (rest, ts) ← pIdents n (ts.drop 1)
      some (a :: rest, ts)
    else some ([a], ts)

def joinKind : List Tok → Option (Str × List Tok)
  | Tok.kw [74, 79, 73, 78] :: ts => some ([73, 78, 78, 69, 82], ts)
  | Tok.kw [73, 78, 78, 69, 82] :: Tok.kw [74, 79, 73, 78] :: ts => some ([73, 78, 78, 69, 82], ts)
  | Tok.kw [76, 69, 70, 84] :: Tok.kw [79, 85, 84, 69, 82] :: Tok.kw [74, 79, 73, 78] :: ts => some ([76, 69, 70, 84], ts)
  | Tok.kw [76, 69, 70, 84] :: Tok.kw [74, 79, 73, 78] :: ts => some ([76, 69, 70, 84], ts)
  | Tok.kw [82, 73, 71, 72, 84] :: Tok.kw [79, 85, 84, 69, 82] :: Tok.kw [74, 79, 73, 78] :: ts => some ([82, 73, 71, 72, 84], ts)
  | Tok.kw [82, 73, 71, 72, 84] :: Tok.kw [74, 79, 73, 78] :: ts => some ([82, 73, 71, 72, 84], ts)
  | Tok.kw [70, 85, 76, 76] :: Tok.kw [79, 85, 84, 69, 82] :: Tok.kw [74, 79, 73, 78] :: ts => some ([70, 85, 76, 76], ts)
  | Tok.kw [70, 85, 76, 76] :: Tok.kw [74, 79, 73, 78] :: ts => some ([70, 85, 76, 76], ts)
  | Tok.kw [67, 82, 79, 83, 83] :: Tok.kw [74, 79, 73, 78] :: ts => some ([67, 82, 79, 83, 83], ts)
  | _ => none

def pJoins : Nat → P (List Join)
  | 0, _ => none
  | n + 1, ts =>
    match joinKind ts with
    | none => some ([], ts)
    | some (kind, ts) => do
      let (t, ts) ← identChain n ts
      let (alias, ts) ← pAlias ts
      let (on, usingCols, ts) ← if isKw [79, 78] ts then do
          let (e, ts) ← pExpr nr n (ts.drop 1)
          some (some e, [], ts)
        else if isKw [85, 83, 73, 78, 71] ts then do
          let ((), ts) ← expectSym [40] (ts.drop 1)
          let (cs, ts) ← pIdents n ts
          let ((), ts) ← expectSym [41] ts
          some (none, cs, ts)
        else some (none, [], ts)
      let (rest, ts) ← pJoins n ts
      some ({ kind, table := t, alias, on, usingCols } :: rest, ts)

def optClause {α} (k : Str) (p : P α) : P (Option α) := fun ts =>
  if isKw k ts then (p (ts.drop 1)).map (fun (a, ts) => (some a, ts)) else some (none, ts)

def pSelect (n : Nat) : P Select := fun ts => do
  let ((), ts) ← expectKw [83, 69, 76, 69, 67, 84] ts
  let (distinct, distinctOn, ts) ← if isKw [68, 73, 83, 84, 73, 78, 67, 84] ts then
      if isKw [79, 78] (ts.drop 1) then do
        let ((), ts) ← expectSym [40] (ts.drop 2)
        let (on, ts) ← pList nr n ts
        let ((), ts) ← expectSym [41] ts
        some (false, on, ts)
      else some (true, [], ts.drop 1)
    else some (false, [], ts)
  let (items, ts) ← pItems nr n ts
  let (source, ts) ← if isKw [70, 82, 79, 77] ts then do
      let (t, ts) ← identChain n (ts.drop 1)
      let (a, ts) ← pAlias ts
      some (some (t, a), ts)
    else some (none, ts)
  let (joins, ts) ← pJoins nr n ts
  let (where_, ts) ← optClause [87, 72, 69, 82, 69] (pExpr nr n) ts
  let (groupBy, ts) ← if isKw [71, 82, 79, 85, 80] ts then do
      let ((), ts) ← expectKw [66, 89] (ts.drop 1)
      pList nr n ts
    else some ([], ts)
  let (having, ts) ← optClause [72, 65, 86, 73, 78, 71] (pExpr nr n) ts
  let (qualify, ts) ← optClause [81, 85, 65, 76, 73, 70, 89] (pExpr nr n) ts
  let (orderBy, ts) ← if isKw [79, 82, 68, 69, 82] ts then do
      let ((), ts) ← expectKw [66, 89] (ts.drop 1)
      pOrderList nr n ts
    else some ([], ts)
  let (limit, ts) ← if isKw [76, 73, 77, 73, 84] ts then
      match ts.drop 1 with
      | Tok.num s :: ts => some (some s, ts)
      | _ => none
    else some (none, ts)
  some ({ distinct, distinctOn, items, source, joins, where_, groupBy, having, qualify, orderBy,
          limit }, ts)

/-- `UNION [ALL] select`, repeated. -/
def pUnions : Nat → P (List (Str × Select))
  | 0, _ => none
  | n + 1, ts =>
    if isKw [85, 78, 73, 79, 78] ts then do
      let (op, ts) := if isKw [65, 76, 76] (ts.drop 1) then ([85, 78, 73, 79, 78, 32, 65, 76, 76], ts.drop 2)
                      else ([85, 78, 73, 79, 78], ts.drop 1)
      let (op, ts) := match ts with
        | Tok.kw [66, 89] :: Tok.ident [110, 97, 109, 101] :: ts => (op ++ [32, 66, 89, 32, 78, 65, 77, 69], ts)
        | _ => (op, ts)
      let (s, ts) ← pSelect nr n ts
      let (rest, ts) ← pUnions n ts
      some ((op, s) :: rest, ts)
    else some ([], ts)

def pCompound (n : Nat) : P Compound := fun ts => do
  let (first, ts) ← pSelect nr n ts
  let (rest, ts) ← pUnions nr n ts
  some ({ first, rest }, ts)

def pCtes : Nat → P (List (Str × Compound))
  | 0, _ => none
  | n + 1, ts => do
    let (name, ts) ← ident ts
    let ((), ts) ← expectKw [65, 83] ts
    let ((), ts) ← expectSym [40] ts
    let (s, ts) ← pCompound nr n ts
    let ((), ts) ← expectSym [41] ts
    if isSym [44] ts then do
      let (rest, ts) ← pCtes n (ts.drop 1)
      some ((name, s) :: rest, ts)
    else some ([(name, s)], ts)

/-- A whole model: optional `WITH`, one select, an optional `;`, nothing else. -/
def parseTokens (n : Nat) (ts : List Tok) : Option Query := do
  let (ctes, ts) ← if isKw [87, 73, 84, 72] ts then pCtes nr n (ts.drop 1) else some ([], ts)
  let (body, ts) ← pCompound nr n ts
  let ts := if isSym [59] ts then ts.drop 1 else ts
  if ts.isEmpty then some { ctes, body } else none

end

/-- What a SQL text in the fragment is, as a tree, under a dialect's NULL-order rule `nr` (see
`defaultNullsFirst`); `none` outside the fragment. The text is given as code points: this is
what a per-model proof evaluates. -/
def parseCodesIn (nr : Nat) (cs : Str) : Option Query := do
  let ts ← lexCodes cs
  parseTokens nr (4 * ts.length + 8) ts

/-- DuckDB's rule (NULLs last), the default. -/
def parseCodes (cs : Str) : Option Query := parseCodesIn 2 cs

/-- The same, from a `String`, for the executable. -/
def parseSqlIn (nr : Nat) (s : String) : Option Query := parseCodesIn nr (s.toList.map Char.toNat)

def parseSql (s : String) : Option Query := parseSqlIn 2 s

end Sql
