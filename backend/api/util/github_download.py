from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from httpx import AsyncClient


if TYPE_CHECKING:
    pass


class GitHubDownloadError(ValueError):
    pass


@dataclass
class GitHubRepoInfo:
    owner: str
    repo: str
    branch: str | None
    subpath: str | None


# Rate limit: 60 requests/hour for unauthenticated requests
MAX_DOWNLOAD_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB

# Directories to exclude when auto-detecting contracts
EXCLUDED_DIRS = {
    'test',
    'tests',
    'testing',
    'script',
    'scripts',
    'node_modules',
    'lib',  # Usually dependencies in Foundry projects
    'dependencies',
    'cache',
    '.git',
    '.github',
    'out',
    'build',
    'artifacts',
    'coverage',
    'docs',
    'doc',
}

# Priority order for contract directories (higher priority first)
CONTRACT_DIR_PRIORITY = [
    'contracts',
    'src',
    'solidity',
    'sol',
]


def parse_github_url(url: str) -> GitHubRepoInfo:
    """Parse a GitHub URL and extract owner, repo, branch, and optional subpath.

    Supported formats:
    - https://github.com/owner/repo
    - https://github.com/owner/repo/tree/branch
    - https://github.com/owner/repo/tree/branch/path/to/folder
    """
    url = url.strip()

    # Remove trailing slash
    url = url.rstrip('/')

    # Pattern: https://github.com/owner/repo[/tree/branch[/path/to/folder]]
    pattern = r'^https?://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+)(?:/(.+))?)?$'
    match = re.match(pattern, url)

    if not match:
        msg = 'Invalid GitHub URL format. Expected: https://github.com/owner/repo[/tree/branch[/path]]'
        raise GitHubDownloadError(msg)

    owner, repo, branch, subpath = match.groups()

    # Remove .git suffix if present
    if repo.endswith('.git'):
        repo = repo[:-4]

    return GitHubRepoInfo(
        owner=owner,
        repo=repo,
        branch=branch,
        subpath=subpath,
    )


async def download_github_repo(url: str, *, auto_detect_contracts: bool = True) -> tuple[bytes, str]:
    """Download a GitHub repository as a zip file.

    Args:
        url: GitHub repository URL
        auto_detect_contracts: If True, automatically detect and extract only contract directories

    Returns:
        Tuple of (zip_bytes, suggested_filename)
    """
    info = parse_github_url(url)

    # Determine the ref (branch) to download
    ref = info.branch

    if not ref:
        # Get default branch from API
        ref = await _get_default_branch(info.owner, info.repo)

    # Download zipball
    zip_bytes = await _download_zipball(info.owner, info.repo, ref)

    # If subpath is specified, extract only that subdirectory
    if info.subpath:
        zip_bytes = _extract_subpath(zip_bytes, info.subpath, info.repo, ref)
        filename = f'{info.repo}-{info.subpath.replace("/", "-")}.zip'
    elif auto_detect_contracts:
        # Auto-detect and extract only contract files
        zip_bytes, detected_path = _extract_contracts_only(zip_bytes)
        if detected_path:
            filename = f'{info.repo}-{detected_path.replace("/", "-")}.zip'
        else:
            filename = f'{info.repo}-contracts.zip'
    else:
        filename = f'{info.repo}-{ref}.zip'

    return zip_bytes, filename


async def _get_default_branch(owner: str, repo: str) -> str:
    """Get the default branch of a repository."""
    async with AsyncClient() as client:
        response = await client.get(
            f'https://api.github.com/repos/{owner}/{repo}',
            headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'evmbench',
            },
            follow_redirects=True,
        )

        if response.status_code == 404:
            msg = f'Repository not found: {owner}/{repo}'
            raise GitHubDownloadError(msg)

        if response.status_code == 403:
            msg = 'GitHub API rate limit exceeded. Please try again later.'
            raise GitHubDownloadError(msg)

        if response.status_code != 200:
            msg = f'Failed to fetch repository info: HTTP {response.status_code}'
            raise GitHubDownloadError(msg)

        data = response.json()
        return str(data.get('default_branch', 'main'))


async def _download_zipball(owner: str, repo: str, ref: str) -> bytes:
    """Download a zipball from GitHub."""
    async with AsyncClient() as client:
        response = await client.get(
            f'https://api.github.com/repos/{owner}/{repo}/zipball/{ref}',
            headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'evmbench',
            },
            follow_redirects=True,
            timeout=60.0,
        )

        if response.status_code == 404:
            msg = f'Branch or repository not found: {owner}/{repo}@{ref}'
            raise GitHubDownloadError(msg)

        if response.status_code == 403:
            msg = 'GitHub API rate limit exceeded. Please try again later.'
            raise GitHubDownloadError(msg)

        if response.status_code != 200:
            msg = f'Failed to download repository: HTTP {response.status_code}'
            raise GitHubDownloadError(msg)

        content = response.content

        if len(content) > MAX_DOWNLOAD_SIZE_BYTES:
            msg = f'Repository is too large (>{MAX_DOWNLOAD_SIZE_BYTES // 1024 // 1024} MB)'
            raise GitHubDownloadError(msg)

        return content


def _extract_subpath(zip_bytes: bytes, subpath: str, repo: str, ref: str) -> bytes:
    """Extract a subdirectory from a zip file and repackage it."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            # GitHub zipballs have a top-level directory like "owner-repo-sha/"
            # Find it first
            top_level = None
            for name in zf.namelist():
                if '/' in name:
                    top_level = name.split('/')[0]
                    break

            if not top_level:
                msg = 'Invalid zip structure from GitHub'
                raise GitHubDownloadError(msg)

            # The subpath inside the zip is: top_level/subpath/
            target_prefix = f'{top_level}/{subpath}'
            if not target_prefix.endswith('/'):
                target_prefix += '/'

            # Check if subpath exists
            matching_files = [n for n in zf.namelist() if n.startswith(target_prefix)]
            if not matching_files:
                msg = f'Subdirectory not found: {subpath}'
                raise GitHubDownloadError(msg)

            # Create new zip with extracted content
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as out_zf:
                for name in matching_files:
                    if name == target_prefix:  # Skip the directory entry itself
                        continue

                    # Remove the prefix to flatten the structure
                    new_name = name[len(target_prefix) :]
                    if not new_name:
                        continue

                    # Read and write the file content
                    content = zf.read(name)
                    out_zf.writestr(new_name, content)

            return output.getvalue()
    except zipfile.BadZipFile as exc:
        msg = 'Invalid zip file from GitHub'
        raise GitHubDownloadError(msg) from exc


def _is_excluded_path(path: str) -> bool:
    """Check if a path should be excluded from contract extraction."""
    parts = path.lower().split('/')
    for part in parts:
        if part in EXCLUDED_DIRS:
            return True
    return False


def _find_contract_directories(zf: zipfile.ZipFile, top_level: str) -> list[str]:
    """Find directories containing Solidity files, prioritizing known contract dirs."""
    sol_files: list[str] = []
    dirs_with_sol: set[str] = set()

    for name in zf.namelist():
        if not name.startswith(top_level + '/'):
            continue
        # Get the path relative to top_level
        rel_path = name[len(top_level) + 1:]
        if not rel_path:
            continue

        # Check if it's a .sol file and not in excluded directories
        if rel_path.lower().endswith('.sol') and not _is_excluded_path(rel_path):
            sol_files.append(rel_path)
            # Get the directory containing this file
            if '/' in rel_path:
                dir_path = rel_path.rsplit('/', 1)[0]
                # Get the top-level directory of this path
                top_dir = dir_path.split('/')[0]
                dirs_with_sol.add(top_dir)
            else:
                # .sol file in root
                dirs_with_sol.add('')

    if not sol_files:
        return []

    # Check for priority contract directories
    for priority_dir in CONTRACT_DIR_PRIORITY:
        if priority_dir in dirs_with_sol:
            return [priority_dir]

    # If no priority dirs found, return all directories with .sol files
    # but prefer directories that are specifically for contracts
    if '' in dirs_with_sol:
        # Files in root - return empty to include all non-excluded .sol files
        return ['']

    return sorted(dirs_with_sol)


def _extract_contracts_only(zip_bytes: bytes) -> tuple[bytes, str | None]:
    """Extract only Solidity contract files from a zip, auto-detecting contract directories.

    Returns:
        Tuple of (new_zip_bytes, detected_directory_name)
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            # Find top-level directory (GitHub format: owner-repo-sha/)
            top_level = None
            for name in zf.namelist():
                if '/' in name:
                    top_level = name.split('/')[0]
                    break

            if not top_level:
                msg = 'Invalid zip structure from GitHub'
                raise GitHubDownloadError(msg)

            # Find contract directories
            contract_dirs = _find_contract_directories(zf, top_level)

            if not contract_dirs:
                msg = 'No Solidity contracts found in repository (excluding test/script directories)'
                raise GitHubDownloadError(msg)

            # Create new zip with only contract files
            output = io.BytesIO()
            files_added = 0

            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as out_zf:
                for name in zf.namelist():
                    if not name.startswith(top_level + '/'):
                        continue

                    rel_path = name[len(top_level) + 1:]
                    if not rel_path or name.endswith('/'):
                        continue

                    # Skip non-solidity files
                    if not rel_path.lower().endswith('.sol'):
                        continue

                    # Skip excluded directories
                    if _is_excluded_path(rel_path):
                        continue

                    # If we have specific contract directories, filter to those
                    if contract_dirs and contract_dirs != ['']:
                        in_contract_dir = False
                        for cdir in contract_dirs:
                            if rel_path.startswith(cdir + '/') or rel_path == cdir:
                                in_contract_dir = True
                                break
                        if not in_contract_dir:
                            continue

                    # Add file to output zip
                    content = zf.read(name)
                    out_zf.writestr(rel_path, content)
                    files_added += 1

            if files_added == 0:
                msg = 'No Solidity contracts found after filtering'
                raise GitHubDownloadError(msg)

            detected_path = contract_dirs[0] if contract_dirs and contract_dirs != [''] else None
            return output.getvalue(), detected_path

    except zipfile.BadZipFile as exc:
        msg = 'Invalid zip file from GitHub'
        raise GitHubDownloadError(msg) from exc
