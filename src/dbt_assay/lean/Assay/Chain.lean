import Assay.Rules
/-!
# Layer 2: what lets a certificate chain one rule into the next

A model is rarely one join. Its grain survives a chain of joins and filters when each step keeps
the grain unique and not null, so these lemmas carry `NotNull` through each step and give the left
join its grain rule. `group by` makes its key unique by construction. A unique, not-null sort key
makes a pick total. Each is used by the per-model files `assay prove` writes.
-/
namespace Assay

theorem notnull_merge {c : String} {a r : Row} (h : (a c).isSome = true) :
    ((merge a r) c).isSome = true := by
  unfold merge; cases e : a c with
  | none => rw [e] at h; simp at h
  | some v => simp

/-- A column never null on the left is never null after an inner join. -/
theorem notnull_through_join {c : String} {lk rk : List String} {L R : Table}
    (h : NotNull c L) : NotNull c (innerJoin lk rk L R) := by
  intro x hx
  simp only [innerJoin, List.mem_flatMap, List.mem_map] at hx
  obtain ⟨a, ha, r, -, rfl⟩ := hx
  exact notnull_merge (h a ha)

theorem mem_leftRow {x l : Row} {ms : List Row} (hx : x ∈ leftRow l ms) :
    x = l ∨ ∃ r ∈ ms, x = merge l r := by
  cases ms with
  | nil => simp [leftRow] at hx; exact Or.inl hx
  | cons m ms =>
    simp only [leftRow, List.mem_map] at hx
    obtain ⟨r, hr, rfl⟩ := hx
    exact Or.inr ⟨r, hr, rfl⟩

/-- A column never null on the left is never null after a left join. -/
theorem notnull_through_left_join {c : String} {lk rk : List String} {L R : Table}
    (h : NotNull c L) : NotNull c (leftJoin lk rk L R) := by
  intro x hx
  simp only [leftJoin, List.mem_flatMap] at hx
  obtain ⟨a, ha, hx⟩ := hx
  rcases mem_leftRow hx with rfl | ⟨r, -, rfl⟩
  · exact h x ha
  · exact notnull_merge (h a ha)

theorem notnull_through_filter {c : String} {p : Row → Bool} {t : Table}
    (h : NotNull c t) : NotNull c (filterT p t) :=
  fun x hx => h x (List.mem_filter.mp hx).1

theorem notnull_all_through_join {g lk rk : List String} {L R : Table}
    (h : ∀ c ∈ g, NotNull c L) : ∀ c ∈ g, NotNull c (innerJoin lk rk L R) :=
  fun c hc => notnull_through_join (h c hc)

theorem notnull_all_through_left_join {g lk rk : List String} {L R : Table}
    (h : ∀ c ∈ g, NotNull c L) : ∀ c ∈ g, NotNull c (leftJoin lk rk L R) :=
  fun c hc => notnull_through_left_join (h c hc)

theorem notnull_all_through_filter {g : List String} {p : Row → Bool} {t : Table}
    (h : ∀ c ∈ g, NotNull c t) : ∀ c ∈ g, NotNull c (filterT p t) :=
  fun c hc => notnull_through_filter (h c hc)

/-- **grain_through_left_join.** The left join keeps the grain too: each left row becomes
exactly one row, carrying its own grain values. -/
theorem grain_through_left_join {g lk rk us : List String} {L R : Table}
    (hl : Unique g L) (hn : ∀ c ∈ g, NotNull c L)
    (hcover : ∀ c ∈ us, c ∈ rk) (hu : Unique us R) :
    Unique g (leftJoin lk rk L R) := by
  have hu' := Unique.mono hcover hu
  unfold Unique leftJoin
  rw [List.pairwise_flatMap]
  constructor
  · intro l _
    apply pairwise_of_length_le_one
    have h1 := matches_le_one (lk := lk) hu' l
    generalize R.filter (joinMatch lk rk l) = ms at h1 ⊢
    match ms, h1 with
    | [], _ => simp [leftRow]
    | [_], _ => simp [leftRow]
    | _ :: _ :: _, h => simp at h
  · unfold Unique at hl
    refine List.Pairwise.imp_of_mem ?_ hl
    intro a b ha hb hab x hx y hy
    have na : ∀ c ∈ g, (a c).isSome = true := fun c hc => hn c hc a ha
    have nb : ∀ c ∈ g, (b c).isSome = true := fun c hc => hn c hc b hb
    have kx : keyOf g x = keyOf g a := by
      rcases mem_leftRow hx with rfl | ⟨r, -, rfl⟩
      · rfl
      · exact keyOf_merge na
    have ky : keyOf g y = keyOf g b := by
      rcases mem_leftRow hy with rfl | ⟨r, -, rfl⟩
      · rfl
      · exact keyOf_merge nb
    rw [kx, ky]
    exact hab

/-- In a pairwise list, two members are the same position or related one way or the other. -/
theorem pairwise_mem_cases {α} {P : α → α → Prop} :
    ∀ {xs : List α}, xs.Pairwise P → ∀ {a b}, a ∈ xs → b ∈ xs → a = b ∨ P a b ∨ P b a
  | [], _, _, _, ha, _ => by simp at ha
  | x :: xs, hp, a, b, ha, hb => by
    have ⟨hx, hxs⟩ := List.pairwise_cons.mp hp
    rcases List.mem_cons.mp ha with rfl | ha' <;> rcases List.mem_cons.mp hb with rfl | hb'
    · exact Or.inl rfl
    · exact Or.inr (Or.inl (hx b hb'))
    · exact Or.inr (Or.inr (hx a ha'))
    · exact pairwise_mem_cases hxs ha' hb'

/-- A sort key column unique and never null makes the order total within every partition. -/
theorem total_of_unique {part ord : List String} {k : String} {t : Table}
    (hk : k ∈ ord) (hu : Unique [k] t) (hn : NotNull k t) : TotalWithin part ord t := by
  intro a ha b hb _ ho
  have ek : a k = b k := (keyOf_eq_iff ord a b).mp ho k hk
  rcases pairwise_mem_cases hu ha hb with h | h | h
  · exact h
  · exact absurd (by simp [keyOf, ek]) (h (by intro v hv; simp [keyOf] at hv; rw [hv]; exact hn a ha))
  · exact absurd (by simp [keyOf, ek]) (h (by intro v hv; simp [keyOf] at hv; rw [hv, ← ek]; exact hn a ha))

end Assay

namespace Assay

theorem keyOf_project {g : List String} {r : Row} : keyOf g (project g r) = keyOf g r := by
  rw [keyOf_eq_iff]; intro c hc; simp [project, hc]

/-- **group_by_unique.** A `group by g` is unique on `g`, whatever it reads. -/
theorem group_by_unique (g : List String) (t : Table) : Unique g (groupBy g t) := by
  unfold Unique groupBy
  rw [List.pairwise_filterMap]
  have nd := distinctKeys_nodup g t
  refine List.Pairwise.imp ?_ nd
  intro a a' hne b hb b' hb' _ heq
  simp only [Option.map_eq_some_iff] at hb hb'
  obtain ⟨r, hr, rfl⟩ := hb
  obtain ⟨r', hr', rfl⟩ := hb'
  have ka : keyOf g r = a := by simpa using List.find?_some hr
  have ka' : keyOf g r' = a' := by simpa using List.find?_some hr'
  rw [keyOf_project, keyOf_project, ka, ka'] at heq
  exact hne heq

theorem inj_of_nodup_map {α β} {f : α → β} :
    ∀ {l : List α}, (l.map f).Nodup → ∀ x ∈ l, ∀ y ∈ l, f x = f y → x = y
  | [], _, x, hx, _, _, _ => by simp at hx
  | a :: l, hn, x, hx, y, hy, hxy => by
    simp only [List.map_cons, List.nodup_cons, List.mem_map, not_exists, not_and] at hn
    rcases List.mem_cons.mp hx with rfl | hx' <;> rcases List.mem_cons.mp hy with rfl | hy'
    · rfl
    · exact absurd hxy.symm (hn.1 y hy')
    · exact absurd hxy (hn.1 x hx')
    · exact inj_of_nodup_map hn.2 x hx' y hy' hxy

theorem keys_nodup {k : List String} {t : Table} (hu : Unique k t)
    (hn : ∀ c ∈ k, NotNull c t) : (t.map (keyOf k)).Nodup := by
  unfold List.Nodup
  rw [List.pairwise_map]
  refine List.Pairwise.imp_of_mem ?_ hu
  intro a b ha _ h
  apply h
  intro v hv
  simp only [keyOf, List.mem_map] at hv
  obtain ⟨c, hc, rfl⟩ := hv
  exact hn c hc a ha

/-- **incremental_equals_full_refresh.** An incremental model that merges on a key equals its
full refresh, as a bag, when:
* the model is row by row (`g`), so a row it has already written is written again identically;
* its key is unique and never null over every row it would ever build (`unique(key)`);
* every row that arrived since the last run passes the incremental filter (`max_lateness` within
  the lookback: `sel` keeps every new row).

Rows the filter re-reads replace themselves; rows it does not re-read stay; every new row is
inserted once. -/
theorem incremental_equals_full_refresh (g : Row → Row) (k : List String) (sel : Row → Bool)
    (Sold Snew : Table)
    (hlate : ∀ r ∈ Snew, sel r = true)
    (hu : Unique k ((Sold ++ Snew).map g))
    (hn : ∀ c ∈ k, NotNull c ((Sold ++ Snew).map g)) :
    (mergeByKey k (Sold.map g) (((Sold ++ Snew).filter sel).map g)).Perm
      ((Sold ++ Snew).map g) := by
  have nd := keys_nodup hu hn
  rw [List.map_map] at nd
  have hnew : Snew.filter sel = Snew := List.filter_eq_self.mpr hlate
  rw [List.filter_append, hnew]
  unfold mergeByKey
  -- a table row survives exactly when the filter did not re-read it
  have keep : (Sold.map g).filter
      (fun t => !((((Sold.filter sel) ++ Snew).map g).any
        (fun b => decide (keyOf k b = keyOf k t)))) = (Sold.filter (fun s => !sel s)).map g := by
    rw [List.filter_map]
    congr 1
    apply List.filter_congr
    intro s hs
    simp only [Function.comp]
    by_cases hsl : sel s = true
    · rw [hsl]
      simp only [Bool.not_true, Bool.not_eq_false', List.any_eq_true, decide_eq_true_eq]
      exact ⟨g s, List.mem_map_of_mem (List.mem_append_left _ (List.mem_filter.mpr ⟨hs, hsl⟩)),
        rfl⟩
    · have hf : sel s = false := by simpa using hsl
      rw [hf]
      simp only [Bool.not_false, Bool.not_eq_true', List.any_eq_false, decide_eq_true_eq]
      intro b hb hk
      obtain ⟨s', hs', rfl⟩ := List.mem_map.mp hb
      have mem : s' ∈ Sold ++ Snew := by
        rcases List.mem_append.mp hs' with h1 | h1
        · exact List.mem_append_left _ (List.mem_filter.mp h1).1
        · exact List.mem_append_right _ h1
      have e := inj_of_nodup_map nd s' mem s (List.mem_append_left _ hs) hk
      subst e
      rcases List.mem_append.mp hs' with h1 | h1
      · exact absurd (List.mem_filter.mp h1).2 hsl
      · -- s is in both the old rows and the new: two positions with one key
        rw [List.map_append] at nd
        have hdis := (List.nodup_append.mp nd).2.2
        exact hdis _ (List.mem_map_of_mem hs) _ (List.mem_map_of_mem h1) rfl
  rw [keep, List.map_append, ← List.append_assoc, List.map_append]
  apply List.Perm.append_right
  rw [← List.map_append]
  exact (List.perm_append_comm.trans (List.filter_append_perm sel Sold)).map g

end Assay
