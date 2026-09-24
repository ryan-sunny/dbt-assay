import Assay.Table
/-!
# The operations, as SQL defines them on bags of rows

Each is the smallest definition that keeps what SQL does to row COUNTS and KEYS, which is what
the rules in `Rules.lean` are about. Values of columns that no rule reads (what a join puts in a
column the left side also has) are settled simply and said here.
-/
namespace Assay

/-- `where p`. -/
def filterT (p : Row → Bool) (t : Table) : Table := t.filter p

/-- Keep only `cols`; every other column reads as NULL. -/
def project (cols : List String) (r : Row) : Row := fun c => if c ∈ cols then r c else none

/-- The join condition `l.lk = r.rk`, column by column: equal, and no NULL (a NULL never
matches, which is why a left join keeps a row with a NULL key unmatched). -/
def joinMatch (lk rk : List String) (l r : Row) : Bool :=
  decide (keyOf lk l = keyOf rk r) && (keyOf lk l).all Option.isSome

/-- A joined row: the left row's value where it has one, the right row's otherwise. -/
def merge (l r : Row) : Row := fun c => match l c with | some v => some v | none => r c

/-- `l inner join r on l.lk = r.rk`. -/
def innerJoin (lk rk : List String) (L R : Table) : Table :=
  L.flatMap (fun l => (R.filter (joinMatch lk rk l)).map (merge l))

/-- `l left join r on l.lk = r.rk`: a left row with no match is kept once, its right side NULL. -/
def leftJoin (lk rk : List String) (L R : Table) : Table :=
  L.flatMap (fun l => match R.filter (joinMatch lk rk l) with
    | [] => [l]
    | ms => ms.map (merge l))

/-- The distinct values of a key, in first-appearance order. -/
def distinctKeys (cols : List String) : Table → List Key
  | [] => []
  | r :: rs => let ks := distinctKeys cols rs
               if keyOf cols r ∈ ks then ks else keyOf cols r :: ks

/-- `group by g`: one row per distinct key, carrying the key. Aggregates are not modelled: the
rules are about how many rows there are and what identifies them. -/
def groupBy (g : List String) (t : Table) : Table :=
  (distinctKeys g t).map (fun k => fun c => match g.idxOf? c with
    | some i => k.getD i none
    | none => none)

/-- A total order on sort keys, given as a Boolean `≤` with the laws a sort needs. The engine's
own order on the values (NULLs first or last included) is one of these. -/
structure KeyOrder where
  le : Key → Key → Bool
  total : ∀ a b, le a b = true ∨ le b a = true
  antisymm : ∀ a b, le a b = true → le b a = true → a = b
  trans : ∀ a b c, le a b = true → le b c = true → le a c = true

/-- The first row of `t` with the smallest `ord` key: what `row_number() = 1` keeps when the order
ties. WHICH row that is depends on the order rows arrive in, and nothing in SQL fixes it. -/
def firstMin (o : KeyOrder) (ord : List String) : Table → Option Row
  | [] => none
  | r :: rs => match firstMin o ord rs with
    | none => some r
    | some m => if o.le (keyOf ord r) (keyOf ord m) then some r else some m

/-- `qualify row_number() over (partition by part order by ord) = 1`: one row per partition. -/
def pick (o : KeyOrder) (part ord : List String) (t : Table) : Table :=
  (distinctKeys part t).filterMap
    (fun k => firstMin o ord (t.filter (fun r => decide (keyOf part r = k))))

end Assay
