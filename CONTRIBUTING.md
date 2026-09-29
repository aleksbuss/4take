# Contributing to 4take

First off, thank you for considering contributing to `4take`! We appreciate your time and effort.

## Code of Conduct

By participating in this project, you agree to abide by our Code of Conduct. Please be respectful and professional in your interactions.

## Local Development Setup

To set up the project locally for development:

1. **Clone the repository**:
   ```bash
   git clone https://github.com/aleksbuss/4take.git
   cd 4take
   ```

2. **Install dependencies using `uv`**:
   We use `uv` for fast dependency management and environment isolation.
   ```bash
   uv sync
   ```

3. **Activate the virtual environment**:
   ```bash
   source .venv/bin/activate
   ```

4. **Run the CLI locally**:
   ```bash
   python -m fourtake.cli --help
   ```

## Testing

We use `pytest` for our test suite. Ensure all tests pass before submitting a pull request.

```bash
pytest
```

## Commit Guidelines

We strictly follow the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification. Your commit messages should follow this structure:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

**Common Types:**
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Changes that do not affect the meaning of the code (white-space, formatting, missing semi-colons, etc)
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `test`: Adding missing tests or correcting existing tests
- `chore`: Changes to the build process or auxiliary tools and libraries such as documentation generation

**Example:**
```
feat(discovery): add validation against OpenRouter API models list
```

## Pull Requests

1. Fork the repository and create your branch from `main`.
2. Make your changes and ensure tests pass.
3. Update `CHANGELOG.md` under the `[Unreleased]` section if your changes are notable.
4. Open a Pull Request with a clear title and description of your changes.
5. Our CI pipeline will automatically run tests and linting (including checking for Conventional Commits compliance).

## CI Status

Our CI pipeline enforces:
- **Linting**: Code formatting and style checks.
- **Testing**: Passing the `pytest` suite.
- **Commit Messages**: Validation of Conventional Commits format.

Pull requests must pass all CI checks before they can be merged.
