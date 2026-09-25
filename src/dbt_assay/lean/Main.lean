import Sql
import Assay
/-!
`assay_sql parse`: the fragment's parse of the SQL on stdin, printed canonically, or `OUTSIDE`.

`assay_sql ops`: run the operations the RULES are proven about (`Assay/Ops.lean`) on tables, so
the conformance suite can check them against the engine too. Input: the same `TABLE` / `ROW` lines,
then `OP innerJoin <L> <R> <lk,..> <rk,..>` | `OP leftJoin ...` | `OP groupBy <T> <g,..>` |
`OP pick <T> <part,..> <ord,..>` (`-` for no columns; DuckDB's default order: ascending, NULLs last), then
`COLS <c> ...`, the columns to print. Output: one `ROW` line per result row.

`assay_sql eval`: run the fragment's meaning on tables and print the result. Input, one item per
line: `TABLE <name> <col> <col> ...`, then `ROW <v>|<v>|...` lines for that table, then `SQL`
and the query on the lines after it. A value is `n` (NULL), `i<digits>` (an integer, `-` allowed),
`b0`/`b1`, or `s<hex>` (a string as hex UTF-8). Output: one `ROW` line per result row in the
same encoding, or `OUTSIDE`.
-/
open Sql

def hexVal (c : Char) : Nat :=
  if c.isDigit then c.toNat - 48 else c.toLower.toNat - 87

def unhex : List Char → List UInt8
  | a :: b :: rest => UInt8.ofNat (hexVal a * 16 + hexVal b) :: unhex rest
  | _ => []

def decodeVal (s : String) : V :=
  match s.toList with
  | 'n' :: _ => none
  | 'b' :: '1' :: _ => some (.bool true)
  | 'b' :: _ => some (.bool false)
  | 'i' :: rest => (String.toInt? (String.ofList rest)).map Val.int
  | 's' :: rest =>
    match String.fromUTF8? (ByteArray.mk (unhex rest).toArray) with
    | some str => some (.str (str.toList.map Char.toNat))
    | none => none
  | _ => none

def hexOf (n : Nat) : String :=
  let d := "0123456789abcdef".toList
  String.ofList [d[n / 16]!, d[n % 16]!]

def encodeVal : V → String
  | none => "n"
  | some (.bool b) => if b then "b1" else "b0"
  | some (.int i) => "i" ++ toString i
  | some (.str s) =>
    "s" ++ String.join ((String.ofList (s.map Char.ofNat)).toUTF8.toList.map (fun b => hexOf b.toNat))

partial def readTables (ls : List String) (acc : List (Str × List Str × Tbl)) :
    List (Str × List Str × Tbl) × List String :=
  match ls with
  | l :: rest =>
    if l.startsWith "TABLE " then
      match (l.drop 6).toString.splitOn " " with
      | name :: cols => readTables rest (acc ++ [(name.toList.map Char.toNat,
          cols.filter (· ≠ "") |>.map (fun c => c.toList.map Char.toNat), [])])
      | [] => (acc, ls)
    else if l.startsWith "ROW " then
      match acc.reverse with
      | (n, cols, rows) :: before =>
        let vals := ((l.drop 4).toString.splitOn "|").map decodeVal
        let row : Row := (cols.zip vals).map (fun (c, v) => ((n, c), v))
        readTables rest ((before.reverse) ++ [(n, cols, rows ++ [row])])
      | [] => (acc, ls)
    else (acc, ls)
  | [] => (acc, [])

/-- A row of the rules' model from a table row: a column not in it reads NULL. -/
def toARow (cols : List Str) (vals : List V) : Assay.Row := fun c =>
  match ((cols.zip vals).find? (fun (k, _) => k == c.toList.map Char.toNat)) with
  | some (_, some (.int i)) => some (.int i)
  | some (_, some (.str s)) => some (.str (String.ofList (s.map Char.ofNat)))
  | _ => none

def ofAVal : Option Assay.Value → V
  | none => none
  | some (.int i) => some (.int i)
  | some (.str s) => some (.str (s.toList.map Char.toNat))

def runOps (tables : List (Str × List Str × Tbl)) (op : List String) (cols : List String) :
    Option (List (List V)) := do
  let tbl := fun (n : String) => (tables.find? (fun (m, _, _) => m == n.toList.map Char.toNat)).map
    (fun (_, cs, rows) => rows.map (fun r => toARow cs (r.map Prod.snd)))
  let keys := fun (s : String) => (s.splitOn ",").filter (fun c => c ≠ "" && c ≠ "-")
  let out ← match op with
    | ["innerJoin", l, r, lk, rk] => do
      pure (Assay.innerJoin (keys lk) (keys rk) (← tbl l) (← tbl r))
    | ["leftJoin", l, r, lk, rk] => do
      pure (Assay.leftJoin (keys lk) (keys rk) (← tbl l) (← tbl r))
    | ["groupBy", t, g] => do pure (Assay.groupBy (keys g) (← tbl t))
    | ["pick", t, part, ord] => do pure (Assay.pick Assay.nullsLast (keys part) (keys ord) (← tbl t))
    | _ => none
  pure (out.map (fun r => cols.map (fun c => ofAVal (r c))))

def main (args : List String) : IO UInt32 := do
  let stdin ← IO.getStdin
  let text ← stdin.readToEnd
  match args with
  | ["parse"] =>
    match Sql.parseSql text with
    | some qry => IO.println (Sql.sQuery qry); pure 0
    | none =>
      match Sql.lex text with
      | none => IO.println "OUTSIDE lex"; pure 2
      | some _ => IO.println "OUTSIDE parse"; pure 2
  | ["eval"] =>
    let lines := text.splitOn "\n"
    let (tables, rest) := readTables lines []
    match rest with
    | "SQL" :: sqlLines =>
      match Sql.parseSql ("\n".intercalate sqlLines) with
      | none => IO.println "OUTSIDE"; pure 2
      | some q =>
        let env := tables.map (fun (n, _, rows) => (n, rows))
        for r in evalQuery env q do
          IO.println ("ROW " ++ "|".intercalate (r.map (fun (_, v) => encodeVal v)))
        pure 0
    | _ => IO.eprintln "no SQL line"; pure 64
  | ["ops"] =>
    let lines := text.splitOn "\n"
    let (tables, rest) := readTables lines []
    let op := (rest.find? (·.startsWith "OP ")).map (fun l => ((l.drop 3).toString.splitOn " ").filter (· ≠ ""))
    let cols := (rest.find? (·.startsWith "COLS ")).map (fun l => ((l.drop 5).toString.splitOn " ").filter (· ≠ ""))
    match op, cols with
    | some op, some cols =>
      match runOps tables op cols with
      | some rows =>
        for r in rows do
          IO.println ("ROW " ++ "|".intercalate (r.map encodeVal))
        pure 0
      | none => IO.println "OUTSIDE"; pure 2
    | _, _ => IO.eprintln "no OP or COLS line"; pure 64
  | _ => IO.eprintln "usage: assay_sql parse|eval|ops < input"; pure 64
