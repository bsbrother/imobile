# Database Migration Guide for imobile Project

## Overview

This guide explains how to properly manage database schema changes using Alembic migrations in the Reflex framework.

## Why Use Alembic Migrations?

Unlike simple SQL scripts, Alembic provides:

1. **Version Control**: Track schema changes over time
2. **Rollback Capability**: Revert changes if needed
3. **Automated Migration**: Detect model changes automatically
4. **Team Collaboration**: Share schema changes with team
5. **Production Safety**: Apply changes systematically

## Project Setup

The imobile project is already initialized with Alembic:
- **Configuration**: `alembic.ini`
- **Migration Scripts**: `alembic/versions/`
- **Environment**: `alembic/env.py`

## Migration Workflow

### 1. Make Changes to Models

When you need to add new fields or modify existing ones, update your model definitions:

```python
# Example: Adding new fields to existing model
class TotalTable(rx.Model, table=True):
    id: int
    user_id: int
    total_market_value: float
    # ... existing fields ...
    
    # NEW FIELDS
    position_percent: float = None  # Position percentage
    withdrawable: float = None      # Withdrawable cash
    last_updated: datetime = None   # Timestamp
```

### 2. Generate Migration Script

**Automatic Detection (Recommended):**
```bash
# Reflex will auto-detect model changes and generate migration
reflex db makemigrations --message "Add realtime fields for mobile app data"
```

**Manual Creation:**
```bash
# Use Alembic directly for manual migrations
alembic revision -m "Description of changes"
# Then edit the generated file manually
```

### 3. Review Generated Migration

Check the generated file in `alembic/versions/`:

```python
def upgrade() -> None:
    """Add new columns"""
    with op.batch_alter_table('total_table', schema=None) as batch_op:
        batch_op.add_column(sa.Column('position_percent', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('withdrawable', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('last_updated', sa.DateTime(), nullable=True))

def downgrade() -> None:
    """Remove added columns"""
    with op.batch_alter_table('total_table', schema=None) as batch_op:
        batch_op.drop_column('last_updated')
        batch_op.drop_column('withdrawable')
        batch_op.drop_column('position_percent')
```

### 4. Apply Migration

```bash
# Apply all pending migrations
reflex db migrate

# Or use Alembic directly:
alembic upgrade head
```

### 5. Verify Migration

```bash
# Check database status
reflex db status

# Or use Alembic directly:
alembic current
alembic history
```

## Common Migration Operations

### Adding Columns

```python
def upgrade() -> None:
    with op.batch_alter_table('table_name', schema=None) as batch_op:
        batch_op.add_column(sa.Column('column_name', sa.Type(), nullable=True))
```

### Modifying Columns

```python
def upgrade() -> None:
    with op.batch_alter_table('table_name', schema=None) as batch_op:
        batch_op.alter_column('column_name',
                             existing_type=sa.String(),
                             type_=sa.Text(),
                             existing_nullable=True)
```

### Dropping Columns

```python
def upgrade() -> None:
    with op.batch_alter_table('table_name', schema=None) as batch_op:
        batch_op.drop_column('column_name')
```

### Creating Indexes

```python
def upgrade() -> None:
    with op.batch_alter_table('table_name', schema=None) as batch_op:
        batch_op.create_index('ix_table_column', ['column_name'], unique=False)
```

### Data Migrations

```python
from sqlalchemy import table, column

def upgrade() -> None:
    # Define a minimal table representation
    my_table = table('table_name',
        column('id', sa.Integer),
        column('old_field', sa.String),
        column('new_field', sa.String)
    )
    
    # Update data
    op.execute(
        my_table.update()
        .where(my_table.c.old_field == 'value')
        .values(new_field='new_value')
    )
```

## Rollback Migrations

Reflex doesn't have built-in downgrade commands. Use Alembic directly:

### Downgrade One Step

```bash
alembic downgrade -1
```

### Downgrade to Specific Version

```bash
alembic downgrade <revision_id>
```

### Downgrade All

```bash
alembic downgrade base
```

## Best Practices

### 1. **Always Use `batch_alter_table` for SQLite**

SQLite has limited ALTER TABLE support. Use `batch_alter_table`:

```python
# ✅ CORRECT
with op.batch_alter_table('table_name', schema=None) as batch_op:
    batch_op.add_column(...)

# ❌ WRONG (may fail on SQLite)
op.add_column('table_name', ...)
```

### 2. **Make Columns Nullable Initially**

When adding columns to existing tables:

```python
# ✅ CORRECT - nullable=True for existing data
batch_op.add_column(sa.Column('new_field', sa.Float(), nullable=True))

# ❌ WRONG - may fail if table has existing rows
batch_op.add_column(sa.Column('new_field', sa.Float(), nullable=False))
```

If you need non-nullable:
1. Add column as nullable
2. Populate with default values
3. Alter column to non-nullable

### 3. **Always Implement `downgrade()`**

Provide a way to rollback:

```python
def upgrade() -> None:
    # Add feature
    pass

def downgrade() -> None:
    # Remove feature (opposite of upgrade)
    pass
```

### 4. **Test Migrations**

```bash
# Apply migration
reflex db migrate

# Test the changes
python tests/test_save_app_data.py

# Rollback to test downgrade (use Alembic)
alembic downgrade -1

# Re-apply
reflex db migrate
```

### 5. **Use Descriptive Messages**

```bash
# ✅ GOOD
reflex db makemigrations --message "Add position_percent and withdrawable to total_table"

# ❌ BAD
reflex db makemigrations --message "update db"
```

### 6. **One Migration Per Feature**

Keep migrations focused:
- ✅ One migration for mobile app fields
- ✅ Another migration for new table
- ❌ Don't mix unrelated changes

### 7. **Check Before Committing**

```bash
# View the generated migration
cat alembic/versions/XXX_description.py

# Test it works
reflex db migrate
alembic downgrade -1
reflex db migrate
```

## Migration for This Project

For the real-time mobile app data feature:

```bash
# 1. Update models (already done in previous implementation)

# 2. The migration file is already created: alembic/versions/001_add_realtime_fields.py

# 3. Apply migration
reflex db migrate

# 4. Verify
reflex db status

# 5. Test with sample data
python tests/test_save_app_data.py
```

## Troubleshooting

### Migration Fails

```bash
# Check current state
reflex db status

# View migration history (use Alembic)
alembic history

# Try manual downgrade
alembic downgrade -1

# Fix the migration file
# Re-apply
reflex db migrate
```

### Multiple Heads

```bash
# Merge migrations (use Alembic)
alembic merge -m "Merge migrations"
```

### Reset Database (Development Only)

```bash
# ⚠️ WARNING: This will delete all data!
rm imobile.db

# Recreate from scratch
reflex db init
reflex db migrate
```

## Production Deployment

### Pre-Deployment Checklist

- [ ] Test migration on development database
- [ ] Test downgrade works
- [ ] Backup production database
- [ ] Review migration script
- [ ] Test application with new schema

### Deploy Steps

```bash
# 1. Backup database
cp production.db production.db.backup

# 2. Apply migrations
export DATABASE_URL="sqlite:///production.db"
reflex db migrate

# 3. Verify
reflex db status

# 4. Test application
python tests/test_save_app_data.py

# 5. If issues occur, rollback (use Alembic)
alembic downgrade -1
```

## References

- [Reflex Database Documentation](https://reflex.dev/docs/database/overview/)
- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- Video: [Reflex Database Migrations Tutorial](https://youtube.com/embed/ITOZkzjtjUA?start=6835&end=8225)

## Reflex DB Command Reference

| Task | Reflex Command | Alembic Alternative |
|------|---------------|---------------------|
| Initialize | `reflex db init` | `alembic init` |
| Create migration | `reflex db makemigrations --message "..."` | `alembic revision -m "..."` |
| Apply migrations | `reflex db migrate` | `alembic upgrade head` |
| Check status | `reflex db status` | `alembic current` |
| View history | N/A | `alembic history` |
| Rollback | N/A | `alembic downgrade -1` |

## Summary

✅ **DO:**
- Use `reflex db makemigrations` to create migrations
- Use `reflex db migrate` to apply migrations
- Implement both `upgrade()` and `downgrade()`
- Use `batch_alter_table` for SQLite
- Make new columns nullable initially
- Test migrations before committing
- Use descriptive migration messages

❌ **DON'T:**
- Edit database directly without migrations
- Skip the downgrade function
- Make breaking changes without data migration
- Forget to test rollback capability (use `alembic downgrade`)
- Mix unrelated changes in one migration

---

**Last Updated**: 2025-10-06
**Status**: ✅ Ready for Use
