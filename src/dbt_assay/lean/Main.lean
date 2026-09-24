import Sql
/-!
`assay_sql parse`: the fragment's parse of the SQL on stdin, printed canonically, or `OUTSIDE`.

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
  | _ => IO.eprintln "usage: assay_sql parse|eval < input"; pure 64
