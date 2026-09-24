/-!
# Tables, as assay reasons about them

A row maps a column name to a value, or to `none`: SQL's NULL. A table is a `List Row`: bag
semantics, so duplicates are allowed and nothing about the order is meaningful to SQL (every rule
below that talks about order says so explicitly).

A key is the list of a row's values in some columns. SQL compares keys with NULLs specially: a
NULL never equals anything, so a key with a NULL in it matches nothing and is exempt from a
`unique` test. `Defined` is "no NULL in it".
-/
namespace Assay

/-- A SQL value. What the rules need is equality, so two cases carry the whole argument. -/
inductive Value where
  | int : Int → Value
  | str : String → Value
  deriving DecidableEq, Repr

abbrev Row := String → Option Value
abbrev Table := List Row
abbrev Key := List (Option Value)

/-- The values of `cols` in `r`, in order. -/
def keyOf (cols : List String) (r : Row) : Key := cols.map r

/-- No NULL in the key. -/
def Defined (k : Key) : Prop := ∀ v ∈ k, v.isSome = true

/-- `unique(cols)` as dbt tests it: no two rows share a key, rows whose key holds a NULL exempt. -/
def Unique (cols : List String) (t : Table) : Prop :=
  t.Pairwise (fun a b => Defined (keyOf cols a) → keyOf cols a ≠ keyOf cols b)

/-- `not_null(col)`. -/
def NotNull (col : String) (t : Table) : Prop := ∀ r ∈ t, (r col).isSome = true

theorem keyOf_eq_iff (cols : List String) (a b : Row) :
    keyOf cols a = keyOf cols b ↔ ∀ c ∈ cols, a c = b c := by
  unfold keyOf
  exact List.map_inj_left

theorem defined_of_subset {us cols : List String} {r : Row}
    (h : ∀ c ∈ us, c ∈ cols) (hd : Defined (keyOf cols r)) : Defined (keyOf us r) := by
  intro v hv
  unfold keyOf at hv
  obtain ⟨c, hc, rfl⟩ := List.mem_map.mp hv
  exact hd _ (List.mem_map.mpr ⟨c, h c hc, rfl⟩)

/-- Uniqueness on fewer columns is uniqueness on more: a unique key stays unique when columns are
added to it. This is what "the join covers the unique key" means. -/
theorem Unique.mono {us cols : List String} {t : Table}
    (h : ∀ c ∈ us, c ∈ cols) (hu : Unique us t) : Unique cols t := by
  unfold Unique at *
  refine List.Pairwise.imp ?_ hu
  intro a b hab hd heq
  apply hab (defined_of_subset h hd)
  rw [keyOf_eq_iff] at heq ⊢
  intro c hc
  exact heq c (h c hc)

end Assay
