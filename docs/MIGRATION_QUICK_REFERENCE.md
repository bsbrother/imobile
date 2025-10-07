# Database Migration Quick Reference

## Common Commands

### Apply Migrations
```bash
reflex db migrate              # Apply all pending migrations
alembic upgrade head           # Alternative using Alembic directly
alembic upgrade +1             # Apply one migration at a time
```

### Rollback Migrations
```bash
alembic downgrade -1           # Rollback one migration
alembic downgrade base         # Rollback all migrations
alembic downgrade <revision>   # Rollback to specific version
```

### Check Status
```bash
reflex db status               # Show database status
alembic current                # Show current migration version
alembic history                # Show migration history
```

### Create New Migration
```bash
reflex db makemigrations --message "Add new field"  # Auto-detect changes
alembic revision -m "Manual migration"              # Create empty migration
```

### Initialize Database
```bash
reflex db init                 # Initialize database and migrations
```

## File Locations

```
alembic/
├── alembic.ini               # Configuration
├── env.py                    # Environment setup
└── versions/                 # Migration scripts
    └── 001_description.py

imobile.db                    # SQLite database (default)
```

## Migration Template

```python
"""Description

Revision ID: XXX
Revises: YYY
Create Date: 2025-XX-XX
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'XXX'
down_revision: Union[str, None] = 'YYY'

def upgrade() -> None:
    # Add changes here
    with op.batch_alter_table('table_name', schema=None) as batch_op:
        batch_op.add_column(sa.Column('field', sa.Type(), nullable=True))

def downgrade() -> None:
    # Reverse changes here
    with op.batch_alter_table('table_name', schema=None) as batch_op:
        batch_op.drop_column('field')
```

## Quick Tips

### ✅ DO
- Use `batch_alter_table` for SQLite
- Make new columns nullable=True
- Test both upgrade and downgrade
- Use descriptive migration messages
- Review generated migrations

### ❌ DON'T
- Edit database manually
- Skip the downgrade function
- Make breaking changes without data migration
- Forget to commit migration files

## Emergency Procedures

### Reset Database (Dev Only)
```bash
rm imobile.db
reflex db init
reflex db migrate
```

### Fix Broken Migration
```bash
alembic downgrade -1           # Rollback (Reflex doesn't have downgrade)
# Edit migration file
reflex db migrate              # Re-apply
```

### Backup Database
```bash
cp imobile.db imobile.db.backup
```

## For This Project

Apply real-time fields migration:
```bash
reflex db migrate
python tests/test_save_app_data.py
```

## Help
```bash
reflex db --help
alembic --help
```

## Documentation
- [Full Migration Guide](DATABASE_MIGRATION_GUIDE.md)
- [Data Mapping](REALTIME_DATA_MAPPING.md)
- [Reflex DB Docs](https://reflex.dev/docs/database/overview/)
