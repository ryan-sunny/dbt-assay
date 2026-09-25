import Assay.Ops
/-!
# A concrete order on keys, so the operations can be RUN

`pick` takes any `KeyOrder`: the rules about it hold whatever order the engine uses. To run the
operations against a real engine (`assay_sql ops`, the conformance suite's check that the
definitions the rules are proven about do what SQL does), one concrete order is needed, with its
laws proven, not assumed.

This is DuckDB's default: values ascending, NULLs after every value. A key is encoded as a list of
integer lists, whose lexicographic order Lean's core library proves linear, and the encoding is
injective, so the laws carry over.
-/
namespace Assay

/-- One value as integers: a tag, then the value. NULL's tag is the largest. -/
def encV : Option Value → List Int
  | none => [3]
  | some (.int i) => [1, i]
  | some (.str s) => 2 :: s.toList.map (fun c => Int.ofNat c.toNat)

theorem encV_inj {a b : Option Value} (h : encV a = encV b) : a = b := by
  match a, b with
  | none, none => rfl
  | some (.int i), some (.int j) =>
    simp only [encV, List.cons.injEq, and_true, true_and] at h
    rw [h]
  | some (.str s), some (.str t) =>
    simp only [encV, List.cons.injEq, true_and] at h
    have hl : s.toList = t.toList :=
      (List.map_inj_right (fun x y hxy => Char.toNat_inj.mp (Int.ofNat_inj.mp hxy))).mp h
    rw [String.toList_inj.mp hl]
  | none, some (.int _) => simp [encV] at h
  | none, some (.str _) => simp [encV] at h
  | some (.int _), none => simp [encV] at h
  | some (.str _), none => simp [encV] at h
  | some (.int _), some (.str _) => simp [encV] at h
  | some (.str _), some (.int _) => simp [encV] at h

def encK (k : Key) : List (List Int) := k.map encV

theorem encK_inj {a b : Key} (h : encK a = encK b) : a = b :=
  (List.map_inj_right (fun _ _ hxy => encV_inj hxy)).mp h

/-- Keys ascending, NULLs last: what DuckDB does with `order by k` unless told otherwise. -/
def nullsLast : KeyOrder where
  le a b := decide (encK a ≤ encK b)
  total a b := by
    rcases List.le_total (encK a) (encK b) with h | h
    · exact Or.inl (decide_eq_true h)
    · exact Or.inr (decide_eq_true h)
  antisymm a b hab hba :=
    encK_inj (List.le_antisymm (of_decide_eq_true hab) (of_decide_eq_true hba))
  trans a b c hab hbc :=
    decide_eq_true (List.le_trans (of_decide_eq_true hab) (of_decide_eq_true hbc))

end Assay
