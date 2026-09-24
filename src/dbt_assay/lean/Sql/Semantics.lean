-- GENERATED from templates/Semantics.lean.in by scripts/gen_lean_sql.py. Edit the template.
import Sql.Parser
/-!
# What the fragment means

A definition of what each construct in the fragment does to tables, with SQL's NULLs and
three-valued logic: `WHERE` and `ON` keep only rows whose condition is TRUE, a comparison with NULL
is UNKNOWN, `NOT IN` a list holding NULL keeps nothing, an aggregate skips NULLs, `count(*)`
counts rows, `GROUP BY` puts NULLs in one group, a left join keeps an unmatched left row with its
right side NULL, and `row_number()` keeps, among rows tied on the order, the first in input order.

It is EXECUTABLE: `assay_sql eval` runs it, so the conformance suite and random differential
tests compare it with a real engine on identical inputs, and any disagreement is a finding about
the engine or about this definition. Benzaken and Contejean's Coq semantics (CPP 2019) is the
reference it follows. Where an engine differs on purpose -- DuckDB's `/` on integers returns a
double, this definition truncates -- the conformance suite says so, construct by construct.

NULLs sort as larger than every value (ASC last, DESC first), which is what Snowflake and
Postgres do.
-/
namespace Sql

inductive Val where
  | int (i : Int)
  | str (s : Str)
  | bool (b : Bool)
  deriving Repr, BEq, DecidableEq, Inhabited

abbrev V := Option Val

/-- A column value, known by the alias it was read under and its name. -/
abbrev Row := List ((Str × Str) × V)
abbrev Tbl := List Row

/-- Three-valued truth: `none` is UNKNOWN. -/
abbrev Tri := Option Bool

def parseInt? (s : Str) : Option Int :=
  if s.isEmpty || s.any (fun c => !(48 ≤ c && c ≤ 57)) then none
  else some (Int.ofNat (s.foldl (fun n c => n * 10 + (c - 48)) 0))

def cmpV : Val → Val → Option Ordering
  | .int a, .int b => some (compare a b)
  | .str a, .str b => some (if a == b then .eq else if List.lt a b then .lt else .gt)
  | .bool a, .bool b => some (compare a b)
  | _, _ => none

def lookup (qual : List Str) (name : Str) (r : Row) : V :=
  match r.find? (fun ((a, c), _) => c == name && (qual.isEmpty || qual.getLast? == some a)) with
  | some (_, v) => v
  | none => none

def truthy : V → Tri
  | some (.bool b) => some b
  | none => none
  | _ => none

def tri2v : Tri → V
  | some b => some (.bool b)
  | none => none

def andT : Tri → Tri → Tri
  | some false, _ => some false
  | _, some false => some false
  | some true, some true => some true
  | _, _ => none

def orT : Tri → Tri → Tri
  | some true, _ => some true
  | _, some true => some true
  | some false, some false => some false
  | _, _ => none

def notT : Tri → Tri
  | some b => some (!b)
  | none => none

def arith (op : Str) : V → V → V
  | some (.int a), some (.int b) =>
    if op == [43] then some (.int (a + b))
    else if op == [45] then some (.int (a - b))
    else if op == [42] then some (.int (a * b))
    else if op == [47] then (if b == 0 then none else some (.int (a.tdiv b)))
    else if op == [37] then (if b == 0 then none else some (.int (a.tmod b)))
    else none
  | some (.str a), some (.str b) => if op == [124, 124] then some (.str (a ++ b)) else none
  | _, _ => none

def cmpOpV (op : Str) (a b : V) : Tri :=
  match a, b with
  | some x, some y =>
    match cmpV x y with
    | some o =>
      if op == [61] then some (o == .eq) else if op == [60, 62] then some (o != .eq)
      else if op == [60] then some (o == .lt) else if op == [60, 61] then some (o != .gt)
      else if op == [62] then some (o == .gt) else if op == [62, 61] then some (o != .lt)
      else none
    | none => none
  | _, _ => none

/-- `LIKE` with `%` and `_`. -/
def likeAux : Nat → Str → Str → Bool
  | 0, _, _ => false
  | _, [], [] => true
  | _, [], _ => false
  | n + 1, 37 :: ps, s => likeAux n ps s || (match s with | [] => false | _ :: t => likeAux n (37 :: ps) t)
  | n + 1, 95 :: ps, _ :: t => likeAux n ps t
  | _ + 1, 95 :: _, [] => false
  | n + 1, p :: ps, c :: t => p == c && likeAux n ps t
  | _ + 1, _ :: _, [] => false

mutual
  partial def eval (r : Row) : Expr → V
    | .col q n => lookup q n r
    | .star _ => none
    | .num s => (parseInt? s).map Val.int
    | .str s => some (.str s)
    | .null => none
    | .bool b => some (.bool b)
    | .bin op a b =>
      if op == [65, 78, 68] then tri2v (andT (truthy (eval r a)) (truthy (eval r b)))
      else if op == [79, 82] then tri2v (orT (truthy (eval r a)) (truthy (eval r b)))
      else if [[61], [60, 62], [60], [60, 61], [62], [62, 61]].contains op then tri2v (cmpOpV op (eval r a) (eval r b))
      else if op == [76, 73, 75, 69] then
        match eval r a, eval r b with
        | some (.str s), some (.str p) => some (.bool (likeAux (s.length + p.length + 2) p s))
        | _, _ => none
      else arith op (eval r a) (eval r b)
    | .un op a =>
      if op == [78, 79, 84] then tri2v (notT (truthy (eval r a)))
      else match eval r a with | some (.int i) => some (.int (-i)) | _ => none
    | .isNull a neg => some (.bool ((eval r a).isNone != neg))
    | .inList a xs neg =>
      let v := eval r a
      let hits := evalList r xs |>.map (fun x => cmpOpV [61] v x)
      let t := hits.foldl orT (some false)
      tri2v (if neg then notT t else t)
    | .between a lo hi neg =>
      let t := andT (cmpOpV [62, 61] (eval r a) (eval r lo)) (cmpOpV [60, 61] (eval r a) (eval r hi))
      tri2v (if neg then notT t else t)
    | .case o ws e => evalCase r (match o with | .none => none | .some x => some (eval r x)) ws e
    | .cast a _ => eval r a
    | .tryCast a _ => eval r a
    | .filtered f _ => eval r f
    | .fn name _ args =>
      if name == [99, 111, 97, 108, 101, 115, 99, 101] then (evalList r args).foldl (fun acc x => acc <|> x) none
      else if name == [110, 117, 108, 108, 105, 102] then
        match evalList r args with
        | [a, b] => if cmpOpV [61] a b == some true then none else a
        | _ => none
      else none
    | .window _ _ _ => none
  partial def evalList (r : Row) : ExprList → List V
    | .nil => []
    | .cons x xs => eval r x :: evalList r xs
  partial def evalCase (r : Row) (o : Option V) : WhenList → OptExpr → V
    | .nil, .none => none
    | .nil, .some e => eval r e
    | .cons c v rest, e =>
      let hit := match o with
        | some ov => cmpOpV [61] ov (eval r c) == some true
        | none => truthy (eval r c) == some true
      if hit then eval r v else evalCase r o rest e
end

def ExprList.toList : ExprList → List Expr
  | .nil => []
  | .cons x xs => x :: toList xs

def OrderList.toList : OrderList → List (Expr × Bool)
  | .nil => []
  | .cons e d r => (e, d) :: toList r

/-- Is this expression (or anything in it) an aggregate call? -/
partial def isAgg : Expr → Bool
  | .fn n _ _ => [[99, 111, 117, 110, 116], [115, 117, 109], [109, 105, 110], [109, 97, 120]].contains n
  | .filtered f _ => isAgg f
  | .bin _ a b => isAgg a || isAgg b
  | .un _ a => isAgg a
  | .cast a _ => isAgg a
  | .tryCast a _ => isAgg a
  | _ => false

def dedup (xs : List V) : List V :=
  xs.foldl (fun acc x => if acc.contains x then acc else acc ++ [x]) []

def minMax (isMax : Bool) (xs : List V) : V :=
  (xs.filterMap id).foldl (fun acc x => match acc with
    | none => some x
    | some y => match cmpV x y with
      | some .lt => if isMax then some y else some x
      | some .gt => if isMax then some x else some y
      | _ => some y) none

/-- An expression over a group of rows: aggregates over all of them, anything else from the
first (it is a group key, the same in every row). -/
partial def evalG (g : Tbl) : Expr → V
  | .filtered (.fn n d args) c => evalG (g.filter (fun r => truthy (eval r c) == some true)) (.fn n d args)
  | .fn n d args =>
    let arg := (ExprList.toList args).head?
    let vals := match arg with
      | some (.star _) => g.map (fun _ => some (.bool true))
      | some a => g.map (fun r => eval r a)
      | none => []
    let vals := if d then dedup vals else vals
    if n == [99, 111, 117, 110, 116] then some (.int (Int.ofNat (vals.filter Option.isSome).length))
    else if n == [115, 117, 109] then
      let xs := vals.filterMap (fun v => match v with | some (.int i) => some i | _ => none)
      if xs.isEmpty then none else some (.int (xs.foldl (· + ·) 0))
    else if n == [109, 105, 110] then minMax false vals
    else if n == [109, 97, 120] then minMax true vals
    else match g.head? with | some r => eval r (.fn n d args) | none => none
  | .bin op a b =>
    let e := Expr.bin op a b
    if isAgg e then
      match g.head? with
      | some r => eval ([(([], [95, 95, 97]), evalG g a), (([], [95, 95, 98]), evalG g b)] ++ r)
                    (.bin op (.col [] [95, 95, 97]) (.col [] [95, 95, 98]))
      | none => eval [(([], [95, 95, 97]), evalG g a), (([], [95, 95, 98]), evalG g b)]
                    (.bin op (.col [] [95, 95, 97]) (.col [] [95, 95, 98]))
    else match g.head? with | some r => eval r e | none => none
  | e => match g.head? with | some r => eval r e | none => none

/-- NULLs are larger than every value. -/
def keyLe (ks : List (V × Bool)) (ks' : List (V × Bool)) : Bool :=
  match ks, ks' with
  | [], [] => true
  | (a, d) :: r, (b, _) :: r' =>
    let o : Ordering := match a, b with
      | none, none => .eq
      | none, some _ => .gt
      | some _, none => .lt
      | some x, some y => (cmpV x y).getD .eq
    let o := if d then o.swap else o
    if o == .lt then true else if o == .gt then false else keyLe r r'
  | _, _ => true

/-- `qualify row_number() over (partition by p order by o) = 1`: the first row of each partition. -/
def pickFirst (part : List Expr) (ord : List (Expr × Bool)) (t : Tbl) : Tbl :=
  let keyOf := fun (r : Row) => part.map (eval r)
  let parts := dedup' (t.map keyOf)
  parts.filterMap fun k =>
    let g := t.filter (fun r => keyOf r == k)
    let first := g.foldl (fun (acc : Option Row) r => match acc with
      | none => some r
      | some b =>
        let kr := ord.map (fun (e, d) => (eval r e, d))
        let kb := ord.map (fun (e, d) => (eval b e, d))
        if keyLe kr kb && !keyLe kb kr then some r else some b) none
    first
where
  dedup' (xs : List (List V)) : List (List V) :=
    xs.foldl (fun acc x => if acc.contains x then acc else acc ++ [x]) []

def aliased (a : Str) (r : Row) : Row := r.map (fun ((_, c), v) => ((a, c), v))

def nullsLike (r : Row) : Row := r.map (fun (k, _) => (k, none))

/-- `FROM`/`JOIN` sources, from the tables given and the CTEs defined so far. -/
def source (env : List (Str × Tbl)) (parts : List Str) (alias : Option Str) : Tbl :=
  let name := parts.getLast?.getD []
  let t := (env.find? (fun (n, _) => n == name)).map Prod.snd |>.getD []
  t.map (aliased (alias.getD name))

def joinStep (env : List (Str × Tbl)) (acc : Tbl) (j : Join) : Tbl :=
  let r := source env j.table j.alias
  let hit := fun (a b : Row) => match j.on with
    | some e => truthy (eval (a ++ b) e) == some true
    | none => true
  let blank := match r.head? with | some b => nullsLike b | none => []
  if j.kind == [76, 69, 70, 84] then
    acc.flatMap fun a =>
      match r.filter (hit a) with
      | [] => [a ++ blank]
      | ms => ms.map (fun b => a ++ b)
  else acc.flatMap fun a => (r.filter (hit a)).map (fun b => a ++ b)

def outCols (items : List (Expr × Option Str)) (t : Tbl) : List (Str × Str) :=
  items.flatMap fun (e, a) => match e with
    | .star _ => (t.head?.map (fun r => r.map Prod.fst)).getD []
    | .col _ n => [([], a.getD n)]
    | _ => [([], a.getD [63])]

def project (items : List (Expr × Option Str)) (value : Expr → V) (r : Row) : Row :=
  items.flatMap fun (e, a) => match e with
    | .star _ => r
    | .col _ n => [(([], a.getD n), value e)]
    | _ => [(([], a.getD [63]), value e)]

def evalSelect (env : List (Str × Tbl)) (s : Select) : Tbl :=
  let base := match s.source with
    | some (parts, a) => source env parts a
    | none => [[]]
  let joined := s.joins.foldl (joinStep env) base
  let kept := match s.where_ with
    | some w => joined.filter (fun r => truthy (eval r w) == some true)
    | none => joined
  let picked := match s.qualify with
    | some (.bin _ (.window (.fn _ _ _) part ord) (.num _)) =>
      pickFirst (ExprList.toList part) (OrderList.toList ord) kept
    | _ => kept
  let grouped := !s.groupBy.isEmpty || s.items.any (fun (e, _) => isAgg e)
  let rows :=
    if grouped then
      let keyOf := fun (r : Row) => s.groupBy.map (eval r)
      let keys := pickFirst.dedup' (picked.map keyOf)
      let groups := if s.groupBy.isEmpty then [picked] else keys.map (fun k => picked.filter (fun r => keyOf r == k))
      let groups := match s.having with
        | some h => groups.filter (fun g => truthy (evalG g h) == some true)
        | none => groups
      groups.map (fun g => project s.items (evalG g) (g.head?.getD []))
    else picked.map (fun r => project s.items (eval r) r)
  if s.distinct then rows.foldl (fun acc x => if acc.contains x then acc else acc ++ [x]) [] else rows

/-- `UNION ALL` keeps every row; `UNION` one of each. The result's columns are the first
select's, matched by position. -/
def evalCompound (env : List (Str × Tbl)) (c : Compound) : Tbl :=
  c.rest.foldl (fun acc (op, s) =>
    let more := evalSelect env s
    let keys := (acc.head? <|> more.head?).map (fun r => r.map Prod.fst)
    let rekey := fun (r : Row) => match keys with
      | some ks => ks.zip (r.map Prod.snd)
      | none => r
    let rel := acc.map rekey ++ more.map rekey
    if op == [85, 78, 73, 79, 78] then rel.foldl (fun a x => if a.contains x then a else a ++ [x]) [] else rel)
    (evalSelect env c.first)

/-- What a query returns on these tables. -/
def evalQuery (tables : List (Str × Tbl)) (q : Query) : Tbl :=
  let env := q.ctes.foldl (fun env (n, c) => env ++ [(n, evalCompound env c)]) tables
  evalCompound env q.body

end Sql
