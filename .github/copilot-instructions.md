# Project Guidelines for imobile Project

imobile project is a web application built with the Reflex framework (https://reflex.dev/) for tracking stock portfolios. It outlines the project structure, coding conventions, and best practices to maintain consistency and facilitate collaboration.

## Overview

imobile is a stock portfolio tracking application that allows users to:
- Create accounts and authenticate
- View and manage their stock portfolios
- Track performance metrics
- Access multilingual support (English and Chinese)


## Python Package Manager
This project use UV (https://docs.astral.sh/uv/) for init virtual environment.

```bash
uv venv --python 3.12           # Init project virtual environment
cp ~/.local/bin/pip* .venv/bin/ # Copy pip, pip3 to .venv/bin/
# When open new terminal, auto run under command in ~/.bashrc, then enter virtual environment:
[ -s "$PWD/.venv/bin/activate" ]] && source $PWD/.venv/bin/activate
# Now you are under virtual environment, you can run python, pip, uv command.
```


## Project Setup
```bash
# Install and init reflex project
pip install reflex              # Added reflex related packages, sync to pyproject.toml, uv.lock
reflex init                     # Init reflex project: created assets/, .web/
reflex db init                  # Init project database: created alembic/, alembic.ini
reflex run                      # Run project
```

## Testing
- Always add tests for new features and update related tests for modified functions.
- ** All test files must at tests/ directory. when all tests pass, remove temp test files. **

## Documentation
- Always update the README.md documentation when making changes to the codebase.
- ** All doc files must at docs/ directory. **


## Database
- Fetch Reflex Database Doc (https://reflex.dev/docs/database/overview/) by context7 MCP.
  Use Using SQLite (reflex project default) db and SQLAlchemy ORM. 
- Fetch Reflex DB Doc (https://reflex.dev/docs/database/overview/migrations) by context7 MCP.
  Do better migration when db schemas changes.

## Styling & Theming and Responsive Design
- **Styling:** Reflex components, Font Awesome
- **Theming:** Reflex theme system with dark/light mode support
- **Responsive Design:** ensure that the application will work well on all device sizes, with optimized layouts for mobile, tablet, and desktop views. The sidebar will be hidden by default on mobile and can be toggled with a hamburger menu, while on desktop it can be collapsed or expanded as needed.


## Project Structure

The project follows the recommended Reflex structure (https://reflex.dev/docs/advanced-onboarding/code-structure/) with some customizations:

```
imobile/
├── __init__.py           # Package marker
├── components/           # Reusable UI components
├── db.py                 # Database models and initialization
├── imobile.py            # Main application entry point
├── pages/                # Application pages
├── states/               # Application state management
└── translations.py       # Language translation strings
```

### Key Components

1. **Main App Module (`imobile.py`)**
   - Defines the main `app` instance as `rx.App()`
   - Imports all necessary modules
   - Sets up routes and page configurations
   - Initializes the database

2. **Pages Package (`pages/`)**
   - Contains individual modules for each page
   - Each page is a function that returns a component
   - Pages are decorated with `@rx.page()` when needed

3. **Components Package (`components/`)**
   - Contains reusable UI elements
   - Components are functions that return Reflex components
   - Organized by functionality (e.g., header, footer, tables)

4. **States Package (`states/`)**
   - Contains state management classes
   - Each state class extends `rx.State`
   - Manages application data and event handlers

5. **Database Module (`db.py`)**
   - Defines SQLAlchemy models
   - Handles database connections and initialization
   - Contains utility functions for database operations

6. **Translations Module (`translations.py`)**
   - Contains translation dictionaries
   - Provides functions for language switching

## Development Guidelines

### Adding New Pages

1. Create a new file in the `pages/` directory
2. Define a function that returns a component
3. Use the `@rx.page()` decorator if needed
4. Import the page in `imobile.py` and add it to the app routes

Example:
```python
# pages/new_page.py
import reflex as rx
from imobile.states.auth_state import AuthState

def new_page():
    return rx.el.div(
        rx.el.h1(AuthState.t.get("new_page_title", "New Page")),
        class_name="container mx-auto p-4",
    )

# In imobile.py
from imobile.pages.new_page import new_page
...
app.add_page(new_page, route="/new-page")
```

### Creating Reusable Components

1. Create a new file in the `components/` directory
2. Define a function that returns a component
3. Import and use the component in your pages

Example:
```python
# components/custom_button.py
import reflex as rx

def custom_button(text: str, on_click=None):
    return rx.el.button(
        text,
        class_name="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded",
        on_click=on_click,
    )

# In a page file
from imobile.components.custom_button import custom_button
...
custom_button("Click Me", on_click=State.handle_click)
```

### State Management

1. Create a new file in the `states/` directory for new state classes
2. Extend the `rx.State` class
3. Define state variables and event handlers
4. Use the `@rx.event()` decorator for event handlers

Example:
```python
# states/new_state.py
import reflex as rx

class NewState(rx.State):
    count: int = 0


    @rx.event
    def increment(self):
        self.count += 1
```

### Accessing Other States

Use the `get_state` API to access other states:

```python
@rx.event(background=True)
async def some_event_handler(self):
    async with self:
        auth_state = await self.get_state(AuthState)
        # Now you can access auth_state properties and methods
```

### Database Operations

1. Define new models in `db.py` by extending the `Base` class
2. Use the provided session management utilities
3. Handle exceptions properly

Example:
```python
# Adding a new record
def add_new_record(user_id: int, data: dict):
    db = SessionLocal()
    try:
        new_record = NewModel(user_id=user_id, **data)
        db.add(new_record)
        db.commit()
        return new_record
    except SQLAlchemyError as e:
        db.rollback()
        print(f"Database error: {e}")
        return None
    finally:
        db.close()
```

### Translations

Add new translation keys to the dictionaries in `translations.py`:

```python
# In translations.py
EN_TEXTS = {
    # ... existing translations
    "new_key": "English text",
}

ZH_TEXTS = {
    # ... existing translations
    "new_key": "中文文本",
}
```

Access translations in components:
```python
AuthState.t.get("new_key", "Default fallback text")
```

## Testing

The project uses pytest for testing. Tests are located in the `tests/` directory.

To run tests:
```bash
pytest --cov=imobile
```

- When adding new features, create corresponding tests in the appropriate test files.
- Always add tests for new features and update related tests for modified functions.
- Always run tests before committing changes.

## Best Practices

1. **Component Reusability**
   - Create small, focused components
   - Use parameters to make components flexible
   - Consider using `@lru_cache` for performance on complex components

2. **State Management**
   - Keep state classes focused on specific functionality
   - Use background events for async operations
   - Properly handle loading states and errors

3. **Code Organization**
   - Follow the established project structure
   - Keep files focused on a single responsibility
   - Use meaningful names for files, functions, and variables

4. **Performance**
   - Minimize unnecessary state updates
   - Use background events for long-running operations
   - Consider memoization for expensive computations

5. **Styling**
   - Use consistent class naming (project uses Tailwind-style classes)
   - Leverage the existing component styles
   - Keep styling close to the components that use it

## Deployment

The application can be deployed using Reflex's deployment options. Refer to the Reflex deployment documentation (https://reflex.dev/docs/hosting/deploy-quick-start/) for details.

## Contributing

1. Follow the project structure and guidelines
2. Write tests for new features
3. Ensure all tests pass before submitting changes
4. Update documentation as needed
