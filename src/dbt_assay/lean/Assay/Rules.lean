import Assay.Ops
/-!
# The rules assay enforces, proven

Each theorem is the statement a check in assay leans on. `RULES_PROVEN` in the Python package
maps each rule to its theorem here, and a test fails when either side moves without the other.
None uses `sorry`, and `#print axioms` on each lists only Lean's standard axioms.
-/
namespace Assay

/-- A pairwise-related list whose elements are pairwise UNrelated has at most one element. -/
theorem length_le_one_of_pairwise {α} {P : α → α → Prop} :
    ∀ {xs : List α}, xs.Pairwise P → (∀ a ∈ xs, ∀ b ∈ xs, ¬ P a b) → xs.length ≤ 1
  | [], _, _ => by simp
  | [_], _, _ => by simp
  | a :: b :: _, hp, hn => by
    have hab : P a b := (List.pairwise_cons.mp hp).1 b (by simp)
    exact absurd hab (hn a (by simp) b (by simp))

theorem joinMatch_key {lk rk : List String} {l r : Row} (h : joinMatch lk rk l r = true) :
    keyOf rk r = keyOf lk l ∧ Defined (keyOf lk l) := by
  unfold joinMatch at h
  simp only [Bool.and_eq_true, decide_eq_true_eq, List.all_eq_true] at h
  exact ⟨h.1.symm, fun v hv => h.2 v hv⟩

/-- A row of the left side meets at most one row of a right side unique on the join key. -/
theorem matches_le_one {lk rk : List String} {R : Table} (hu : Unique rk R) (l : Row) :
    (R.filter (joinMatch lk rk l)).length ≤ 1 := by
  apply length_le_one_of_pairwise (hu.sublist List.filter_sublist)
  intro a ha b hb hab
  have ka := joinMatch_key (List.mem_filter.mp ha).2
  have kb := joinMatch_key (List.mem_filter.mp hb).2
  exact hab (by rw [ka.1]; exact ka.2) (by rw [ka.1, kb.1])

/-- **inner_join_no_fanout.** A join onto a right side that is unique on keys the join covers
cannot produce more rows than the left side has.

Backs `hop_multiplies_rows` (a hop held back because its parent's key is unique) and
`join_fans_out` (a join that does NOT cover the declared key is the case this does not cover). -/
theorem inner_join_no_fanout {lk rk us : List String} {L R : Table}
    (hcover : ∀ c ∈ us, c ∈ rk) (hu : Unique us R) :
    (innerJoin lk rk L R).length ≤ L.length := by
  have hu' := Unique.mono hcover hu
  induction L with
  | nil => simp [innerJoin]
  | cons l ls ih =>
    simp only [innerJoin, List.flatMap_cons, List.length_append, List.length_map,
      List.length_cons] at *
    have := matches_le_one (lk := lk) hu' l
    omega

/-- **left_join_preserves_rows.** A left join onto a right side unique on keys the join covers
keeps exactly the left side's rows: every left row appears once, matched or not.

Backs `hop_multiplies_rows` for a LEFT join held back on its parent's key. -/
theorem left_join_preserves_rows {lk rk us : List String} {L R : Table}
    (hcover : ∀ c ∈ us, c ∈ rk) (hu : Unique us R) :
    (leftJoin lk rk L R).length = L.length := by
  have hu' := Unique.mono hcover hu
  have row : ∀ l : Row, (leftRow l (R.filter (joinMatch lk rk l))).length = 1 := by
    intro l
    have h1 := matches_le_one (lk := lk) hu' l
    generalize R.filter (joinMatch lk rk l) = ms at h1 ⊢
    match ms, h1 with
    | [], _ => rfl
    | [_], _ => rfl
    | _ :: _ :: _, h => simp at h
  induction L with
  | nil => simp [leftJoin]
  | cons l ls ih =>
    simp only [leftJoin, List.flatMap_cons, List.length_append, List.length_cons] at *
    rw [ih, row l]
    omega

/-- A list of at most one element is pairwise anything. -/
theorem pairwise_of_length_le_one {α} {P : α → α → Prop} :
    ∀ {xs : List α}, xs.length ≤ 1 → xs.Pairwise P
  | [], _ => List.Pairwise.nil
  | [_], _ => by simp
  | _ :: _ :: _, h => by simp at h

theorem keyOf_merge {g : List String} {a r : Row} (hn : ∀ c ∈ g, (a c).isSome = true) :
    keyOf g (merge a r) = keyOf g a := by
  rw [keyOf_eq_iff]
  intro c hc
  have := hn c hc
  unfold merge
  cases h : a c with
  | none => rw [h] at this; simp at this
  | some v => rfl

/-- **grain_through_join.** A left side unique and not null on `g`, joined onto a right side
unique on keys the join covers, is still unique on `g`: the join cannot repeat a left row.

Backs the grain assay carries through a join (a child keeps its driving parent's grain). -/
theorem grain_through_join {g lk rk us : List String} {L R : Table}
    (hl : Unique g L) (hn : ∀ c ∈ g, NotNull c L)
    (hcover : ∀ c ∈ us, c ∈ rk) (hu : Unique us R) :
    Unique g (innerJoin lk rk L R) := by
  have hu' := Unique.mono hcover hu
  unfold Unique innerJoin
  rw [List.pairwise_flatMap]
  constructor
  · intro l _
    apply pairwise_of_length_le_one
    rw [List.length_map]
    exact matches_le_one hu' l
  · unfold Unique at hl
    refine List.Pairwise.imp_of_mem ?_ hl
    intro a b ha hb hab x hx y hy
    obtain ⟨r1, -, rfl⟩ := List.mem_map.mp hx
    obtain ⟨r2, -, rfl⟩ := List.mem_map.mp hy
    have na : ∀ c ∈ g, (a c).isSome = true := fun c hc => hn c hc a ha
    have nb : ∀ c ∈ g, (b c).isSome = true := fun c hc => hn c hc b hb
    rw [keyOf_merge na, keyOf_merge nb]
    exact hab

/-- **filter_preserves_unique.** A `where` keeps any uniqueness: it only removes rows.

Backs grain derivation through a filter. -/
theorem filter_preserves_unique {g : List String} {t : Table} (p : Row → Bool)
    (h : Unique g t) : Unique g (filterT p t) :=
  h.sublist List.filter_sublist

/-! ## Picks: `row_number() ... = 1` -/

/-- One step of taking the smallest key: the new key when it is `≤` the smallest so far. -/
def minStep (o : KeyOrder) (k : Key) : Option Key → Option Key
  | none => some k
  | some m => some (if o.le k m then k else m)

theorem minStep_comm (o : KeyOrder) (a b : Key) (z : Option Key) :
    minStep o a (minStep o b z) = minStep o b (minStep o a z) := by
  have tot := o.total; have anti := o.antisymm; have tr := o.trans
  cases z with
  | none =>
    simp only [minStep]
    by_cases hab : o.le a b = true <;> by_cases hba : o.le b a = true <;> simp_all
    · exact anti _ _ hab hba
    · rcases tot a b with h | h <;> simp_all
  | some m =>
    simp only [minStep, Option.some.injEq]
    by_cases hbm : o.le b m = true <;> by_cases ham : o.le a m = true <;>
      by_cases hab : o.le a b = true <;> by_cases hba : o.le b a = true <;>
      simp only [hbm, ham, hab, hba, ite_true, ite_false, Bool.false_eq_true]
    all_goals first
      | rfl
      | exact anti _ _ hab hba
      | exact (anti _ _ hab hba).symm
      | exact absurd (tr _ _ _ hab hbm) ham
      | exact absurd (tr _ _ _ hba ham) hbm
      | (rcases tot a b with h | h <;> simp_all)
      | (rcases tot a m with h | h <;> simp_all)
      | (rcases tot b m with h | h <;> simp_all)

/-- The smallest `ord` key of a group, whatever order its rows arrive in. -/
def minKey (o : KeyOrder) (ord : List String) (g : Table) : Option Key :=
  g.foldr (fun r acc => minStep o (keyOf ord r) acc) none

theorem firstMin_key (o : KeyOrder) (ord : List String) :
    ∀ g : Table, (firstMin o ord g).map (keyOf ord) = minKey o ord g
  | [] => rfl
  | r :: rs => by
    have ih := firstMin_key o ord rs
    simp only [firstMin, minKey, List.foldr_cons] at *
    rw [← ih]
    cases firstMin o ord rs with
    | none => rfl
    | some m => by_cases h : o.le (keyOf ord r) (keyOf ord m) = true <;> simp [h, minStep]

theorem firstMin_mem (o : KeyOrder) (ord : List String) :
    ∀ {g : Table} {m : Row}, firstMin o ord g = some m → m ∈ g
  | [], _, h => by simp [firstMin] at h
  | r :: rs, m, h => by
    simp only [firstMin] at h
    cases hr : firstMin o ord rs with
    | none => rw [hr] at h; simp at h; simp [h]
    | some m' =>
      rw [hr] at h
      by_cases hle : o.le (keyOf ord r) (keyOf ord m') = true <;> simp [hle] at h
      · simp [h]
      · exact List.mem_cons_of_mem _ (h ▸ firstMin_mem o ord hr)

theorem firstMin_none (o : KeyOrder) (ord : List String) :
    ∀ {g : Table}, firstMin o ord g = none ↔ g = []
  | [] => by simp [firstMin]
  | r :: rs => by
    simp only [firstMin, reduceCtorEq, iff_false]
    cases firstMin o ord rs <;> simp; split <;> simp

theorem minKey_perm (o : KeyOrder) (ord : List String) {g g' : Table} (h : g.Perm g') :
    minKey o ord g = minKey o ord g' := by
  unfold minKey
  exact h.foldr_eq' (fun x _ y _ z => minStep_comm o (keyOf ord y) (keyOf ord x) z) none

/-- Two rows agree on `cols` when they agree on the partition and the order keys and `cols` is
made of those. -/
theorem keyOf_eq_of_parts {cols part ord : List String} {a b : Row}
    (hc : ∀ c ∈ cols, c ∈ part ∨ c ∈ ord)
    (hp : keyOf part a = keyOf part b) (ho : keyOf ord a = keyOf ord b) :
    keyOf cols a = keyOf cols b := by
  rw [keyOf_eq_iff] at *
  intro c h
  rcases hc c h with h' | h'
  · exact hp c h'
  · exact ho c h'

theorem distinctKeys_nodup (cols : List String) :
    ∀ t : Table, (distinctKeys cols t).Nodup
  | [] => List.nodup_nil
  | r :: rs => by
    have ih := distinctKeys_nodup cols rs
    simp only [distinctKeys]
    split
    · exact ih
    · exact List.nodup_cons.mpr ⟨by assumption, ih⟩

theorem mem_distinctKeys (cols : List String) (k : Key) :
    ∀ t : Table, k ∈ distinctKeys cols t ↔ ∃ r ∈ t, keyOf cols r = k
  | [] => by simp [distinctKeys]
  | r :: rs => by
    have ih := mem_distinctKeys cols k rs
    simp only [distinctKeys, List.mem_cons, exists_eq_or_imp]
    split
    · rw [ih]; constructor
      · intro h; exact Or.inr h
      · rintro (h | h)
        · subst h; exact (ih.mp (by assumption))
        · exact h
    · rw [List.mem_cons, ih]
      constructor
      · rintro (h | h)
        · exact Or.inl h.symm
        · exact Or.inr h
      · rintro (h | h)
        · exact Or.inl h.symm
        · exact Or.inr h

theorem distinctKeys_perm (cols : List String) {t t' : Table} (h : t.Perm t') :
    (distinctKeys cols t).Perm (distinctKeys cols t') := by
  rw [List.perm_ext_iff_of_nodup (distinctKeys_nodup cols t) (distinctKeys_nodup cols t')]
  intro k
  rw [mem_distinctKeys, mem_distinctKeys]
  constructor
  · rintro ⟨r, hr, e⟩; exact ⟨r, h.mem_iff.mp hr, e⟩
  · rintro ⟨r, hr, e⟩; exact ⟨r, h.mem_iff.mpr hr, e⟩

/-- **pick_is_order_independent.** When every column a dedupe keeps is a partition key or an
order key, the rows it keeps are the same, as a bag, whatever order the input arrives in: two
rows that tie on the order key are identical in everything kept.

Backs `arbitrary_pick`'s `picks_only_keys` exemption (N5). -/
theorem pick_is_order_independent (o : KeyOrder) {part ord cols : List String} {t t' : Table}
    (hc : ∀ c ∈ cols, c ∈ part ∨ c ∈ ord) (h : t.Perm t') :
    ((pick o part ord t).map (keyOf cols)).Perm ((pick o part ord t').map (keyOf cols)) := by
  unfold pick
  rw [List.map_filterMap, List.map_filterMap]
  have same : (fun k => (firstMin o ord (t.filter (fun r => decide (keyOf part r = k)))).map
                  (keyOf cols))
            = (fun k => (firstMin o ord (t'.filter (fun r => decide (keyOf part r = k)))).map
                  (keyOf cols)) := by
    funext k
    have hg : (t.filter (fun r => decide (keyOf part r = k))).Perm
              (t'.filter (fun r => decide (keyOf part r = k))) := h.filter _
    have hk := minKey_perm o ord hg
    rw [← firstMin_key, ← firstMin_key] at hk
    cases e1 : firstMin o ord (t.filter (fun r => decide (keyOf part r = k))) with
    | none =>
      rw [firstMin_none] at e1
      have : t'.filter (fun r => decide (keyOf part r = k)) = [] :=
        List.Perm.eq_nil (e1 ▸ hg).symm
      rw [← firstMin_none (o := o) (ord := ord)] at this
      simp [this]
    | some m =>
      cases e2 : firstMin o ord (t'.filter (fun r => decide (keyOf part r = k))) with
      | none =>
        rw [firstMin_none] at e2
        have : t.filter (fun r => decide (keyOf part r = k)) = [] := List.Perm.eq_nil (e2 ▸ hg)
        rw [← firstMin_none (o := o) (ord := ord)] at this
        rw [this] at e1; cases e1
      | some m' =>
        rw [e1, e2] at hk
        simp only [Option.map_some, Option.some.injEq] at hk ⊢
        have pm := (List.mem_filter.mp (firstMin_mem o ord e1)).2
        have pm' := (List.mem_filter.mp (firstMin_mem o ord e2)).2
        simp only [decide_eq_true_eq] at pm pm'
        exact keyOf_eq_of_parts hc (pm.trans pm'.symm) hk
  rw [same]
  exact (distinctKeys_perm part h).filterMap _

/-- No two rows of one partition tie on the order key: the order is total within a partition. -/
def TotalWithin (part ord : List String) (t : Table) : Prop :=
  ∀ a ∈ t, ∀ b ∈ t, keyOf part a = keyOf part b → keyOf ord a = keyOf ord b → a = b

/-- **pick_total_on_unique_key.** When no two rows of a partition tie on the order key, the
dedupe keeps the same rows -- whole rows, every column -- whatever order the input arrives in.

Backs `arbitrary_pick`'s exemption for a last sort key declared unique. -/
theorem pick_total_on_unique_key (o : KeyOrder) {part ord : List String} {t t' : Table}
    (ht : TotalWithin part ord t) (h : t.Perm t') :
    (pick o part ord t).Perm (pick o part ord t') := by
  unfold pick
  have same : (fun k => firstMin o ord (t.filter (fun r => decide (keyOf part r = k))))
            = (fun k => firstMin o ord (t'.filter (fun r => decide (keyOf part r = k)))) := by
    funext k
    have hg : (t.filter (fun r => decide (keyOf part r = k))).Perm
              (t'.filter (fun r => decide (keyOf part r = k))) := h.filter _
    have hk := minKey_perm o ord hg
    rw [← firstMin_key, ← firstMin_key] at hk
    cases e1 : firstMin o ord (t.filter (fun r => decide (keyOf part r = k))) with
    | none =>
      rw [firstMin_none] at e1
      have : t'.filter (fun r => decide (keyOf part r = k)) = [] :=
        List.Perm.eq_nil (e1 ▸ hg).symm
      rw [← firstMin_none (o := o) (ord := ord)] at this
      rw [this]
    | some m =>
      cases e2 : firstMin o ord (t'.filter (fun r => decide (keyOf part r = k))) with
      | none =>
        rw [firstMin_none] at e2
        have : t.filter (fun r => decide (keyOf part r = k)) = [] := List.Perm.eq_nil (e2 ▸ hg)
        rw [← firstMin_none (o := o) (ord := ord)] at this
        rw [this] at e1; cases e1
      | some m' =>
        rw [e1, e2] at hk
        simp only [Option.map_some, Option.some.injEq] at hk
        have mm := List.mem_filter.mp (firstMin_mem o ord e1)
        have mm' := List.mem_filter.mp (firstMin_mem o ord e2)
        simp only [decide_eq_true_eq] at mm mm'
        exact congrArg some (ht m mm.1 m' (h.mem_iff.mpr mm'.1) (mm.2.trans mm'.2.symm) hk)
  rw [same]
  exact (distinctKeys_perm part h).filterMap _

end Assay
