# Contributing to 4take

First off, thank you for considering contributing to `4take`! We appreciate your time and effort.

## Conduct

Be respectful and professional in issues, pull requests and reviews. Harassment and personal attacks get a contributor removed.

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

## Licensing of Contributions

4take is licensed under the Business Source License 1.1 (see `LICENSE`) and is also licensed
commercially to organizations. A pull request is merged only after its author has agreed to the
[Contributor License Agreement](CLA.md), which lets the Licensor distribute the contribution under
both. If your employer or a client may own what you write, get their permission first.

## Pull Requests

1. Fork the repository and create your branch from `main`.
2. Make your changes and ensure tests pass.
3. Update `CHANGELOG.md` under the `[Unreleased]` section if your changes are notable.
4. Open a Pull Request with a clear title and description of your changes. Say in it if any part
   comes from another project or from a code generator or AI tool.
5. Agree to the [CLA](CLA.md) in the pull request.
6. Our CI pipeline will automatically run tests and linting (including checking for Conventional Commits compliance).

## CI Status

Our CI pipeline enforces:
- **Linting**: `ruff check` style and correctness checks (formatting is not a gate yet).
- **Testing**: Passing the `pytest` suite.
- **Commit Messages**: Validation of Conventional Commits format.

Pull requests must pass all CI checks before they can be merged.
