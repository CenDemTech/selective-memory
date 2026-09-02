# Contributing to Chat Data Portability

Thank you for your interest in contributing to this project! We welcome bug reports, feature requests, and pull requests. This guide will help you get started.

## Getting Started

### Prerequisites
- Python 3.9+
- Git
- Virtual environment tools

### Local Development Setup

1. **Fork the repository** on GitHub
2. **Clone your fork**:
   ```bash
   git clone https://github.com/YOUR-USERNAME/chat-data-portability.git
   cd chat-data-portability
   ```

3. **Create a virtual environment**:
   ```bash
   cd data-portability-app
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

5. **Run the app**:
   ```bash
   python app.py
   ```

6. **Run tests**:
   ```bash
   python -m pytest
   ```

## Reporting Bugs

Found a bug? Please open an issue with:

- **Title**: A clear, concise description (e.g., "Redaction removes extra whitespace")
- **Description**: What happened and what you expected to happen
- **Steps to Reproduce**: Exact steps to trigger the bug
- **Environment**: Your OS, Python version, and browser (if applicable)
- **Logs/Screenshots**: Any error messages or screenshots that help illustrate the issue
- **Sample Data**: If possible, attach a minimal `conversations.json` that reproduces the bug

## Requesting Features

Have an idea for improvement? Open an issue with:

- **Title**: A clear, concise feature description (e.g., "Support for Grok exports")
- **Description**: Why this feature would be useful and how it should work
- **Use Case**: Real-world scenario where this feature helps users
- **Alternatives**: Any alternative approaches you've considered

## Code Style & Standards

### Python
- Use **PEP 8** style guidelines
- Use meaningful variable and function names
- Add docstrings to functions and classes
- Keep functions focused and under 50 lines when possible
- Use type hints for new code

### JavaScript
- Use clear, descriptive variable names
- Add comments for complex logic
- Prefer `const` and `let` over `var`
- Use async/await for asynchronous operations
- Keep functions focused and modular

### HTML/CSS
- Use semantic HTML tags
- Follow BEM naming conventions for CSS classes
- Ensure accessibility (ARIA labels, semantic structure)
- Test on multiple screen sizes

## Submitting Pull Requests

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**:
   - Keep commits focused and atomic
   - Write clear commit messages
   - Run tests before committing

3. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

4. **Open a Pull Request**:
   - Use a clear, descriptive title
   - Reference any related issues (e.g., "Closes #123")
   - Describe what changes you made and why
   - Include screenshots if UI changes are involved
   - Ensure all tests pass

### PR Checklist
- [ ] Code follows project style guidelines
- [ ] Tests pass locally (`python -m pytest`)
- [ ] No breaking changes to existing functionality
- [ ] Documentation updated (if applicable)
- [ ] Commit messages are clear and descriptive
- [ ] No unrelated changes included

## Testing

### Running Tests
```bash
python -m pytest
```

### Writing Tests
- Place tests in the `test_*.py` files
- Use descriptive test names
- Test both success and failure cases
- Include edge cases

### Coverage
We aim for reasonable test coverage. Run:
```bash
python -m pytest --cov=data-portability-app
```

## Code of Conduct

Please be respectful and constructive in all interactions. We're all here to build something useful together.

---

Thank you for contributing! 🎉
