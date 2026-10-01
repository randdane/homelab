# Grocy

**What:** ERP for the house — food stock with expiry dates, shopping lists,
chores, and what things cost.
**Why I care:** It answers "do we already have this" from the shop, and
"what needs eating this week" at home.
**URL:** http://localhost:8092

## Notes

**Default credentials on first run** are `admin` / `admin`. Change them
immediately.

**Overlaps Mealie only at the edges.** Mealie holds recipes and meal plans;
Grocy holds what is physically in the house. They are usually run together,
and Grocy's shopping list is the piece Mealie does not have.

**SQLite in the `config` volume**, hence the stop-during-backup label. The
data is months of hand-entered stock, purchase history, and barcodes — tedious
rather than difficult to recreate, which is exactly the kind of loss that is
most annoying.

**Barcode scanning is the feature that makes it stick.** Without it, keeping
stock accurate by hand is the chore that kills this kind of app — worth
setting up a scanner or the phone app early rather than deciding later that
Grocy "did not work out".
