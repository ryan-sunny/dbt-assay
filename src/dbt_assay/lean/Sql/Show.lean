import Sql.Parser
/-!
# The canonical printing of a tree

A fully bracketed s-expression, one form per constructor, strings quoted with `\"` and `\\`
escaped. Used to report WHERE two trees diverge (the proof itself compares the trees), and
printed identically by assay's Python side from sqlglot's tree.
-/
namespace Sql

def toS (s : Str) : String := String.ofList (s.map Char.ofNat)

def q (s : Str) : String :=
  "\"" ++ ((toS s).replace "\\" "\\\\" |>.replace "\"" "\\\"") ++ "\""

def qs (xs : List Str) : String := "[" ++ " ".intercalate (xs.map q) ++ "]"

mutual
  partial def sExpr : Expr → String
    | .col qual n => "(col " ++ qs qual ++ " " ++ q n ++ ")"
    | .star qual => "(star " ++ qs qual ++ ")"
    | .num s => "(num " ++ q s ++ ")"
    | .str s => "(str " ++ q s ++ ")"
    | .null => "(null)"
    | .bool b => if b then "(true)" else "(false)"
    | .bin op a b => "(bin " ++ q op ++ " " ++ sExpr a ++ " " ++ sExpr b ++ ")"
    | .un op a => "(un " ++ q op ++ " " ++ sExpr a ++ ")"
    | .isNull a neg => "(isnull " ++ sExpr a ++ (if neg then " not" else "") ++ ")"
    | .inList a xs neg => "(in " ++ sExpr a ++ " " ++ sList xs ++ (if neg then " not" else "") ++ ")"
    | .between a lo hi neg => "(between " ++ sExpr a ++ " " ++ sExpr lo ++ " " ++ sExpr hi
        ++ (if neg then " not" else "") ++ ")"
    | .case o ws e => "(case " ++ sOpt o ++ " " ++ sWhens ws ++ " " ++ sOpt e ++ ")"
    | .cast a t => "(cast " ++ sExpr a ++ " " ++ q t ++ ")"
    | .tryCast a t => "(trycast " ++ sExpr a ++ " " ++ q t ++ ")"
    | .filtered f c => "(filter " ++ sExpr f ++ " " ++ sExpr c ++ ")"
    | .fn n d args => "(fn " ++ q n ++ (if d then " distinct " else " ") ++ sList args ++ ")"
    | .window f p o => "(window " ++ sExpr f ++ " " ++ sList p ++ " " ++ sOrder o ++ ")"
  partial def sList : ExprList → String
    | .nil => "[]"
    | .cons x xs => "[" ++ sExpr x ++ " " ++ (sList xs).drop 1
  partial def sWhens : WhenList → String
    | .nil => "[]"
    | .cons c v r => "[(" ++ sExpr c ++ " " ++ sExpr v ++ ") " ++ (sWhens r).drop 1
  partial def sOrder : OrderList → String
    | .nil => "[]"
    | .cons e d r => "[(" ++ sExpr e ++ (if d then " desc" else " asc") ++ ") " ++ (sOrder r).drop 1
  partial def sOpt : OptExpr → String
    | .none => "(none)"
    | .some e => sExpr e
end

def sOptE : Option Expr → String
  | none => "(none)"
  | some e => sExpr e

def sJoin (j : Join) : String :=
  "(join " ++ q j.kind ++ " " ++ qs j.table ++ " " ++ (j.alias.map q |>.getD "(none)") ++ " "
    ++ sOptE j.on ++ " " ++ qs j.usingCols ++ ")"

def sSelect (s : Select) : String :=
  "(select " ++ (if s.distinct then "distinct " else "")
    ++ "[" ++ " ".intercalate (s.items.map fun (e, a) => "(" ++ sExpr e ++ " " ++
        (a.map q |>.getD "(none)") ++ ")") ++ "] "
    ++ (match s.source with
        | some (t, a) => "(from " ++ qs t ++ " " ++ (a.map q |>.getD "(none)") ++ ")"
        | none => "(none)")
    ++ " [" ++ " ".intercalate (s.joins.map sJoin) ++ "] "
    ++ sOptE s.where_ ++ " [" ++ " ".intercalate (s.groupBy.map sExpr) ++ "] "
    ++ sOptE s.having ++ " " ++ sOptE s.qualify ++ " ["
    ++ " ".intercalate (s.orderBy.map fun (e, d) => "(" ++ sExpr e ++ (if d then " desc" else " asc") ++ ")")
    ++ "] " ++ (s.limit.map q |>.getD "(none)") ++ ")"

def sCompound (c : Compound) : String :=
  if c.rest.isEmpty then sSelect c.first
  else "(union " ++ sSelect c.first ++ " [" ++ " ".intercalate (c.rest.map fun (op, s) =>
    "(" ++ q op ++ " " ++ sSelect s ++ ")") ++ "])"

def sQuery (x : Query) : String :=
  "(query [" ++ " ".intercalate (x.ctes.map fun (n, s) => "(" ++ q n ++ " " ++ sCompound s ++ ")")
    ++ "] " ++ sCompound x.body ++ ")"

end Sql
