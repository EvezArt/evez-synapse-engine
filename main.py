import os
import json
import functions_framework
from google.cloud import secretmanager
from github import Github, GithubException


def retrieve_token(project_id: str, secret_id: str = "EVEZ_GITHUB_TOKEN") -> str:
    """Pulls the GitHub PAT from Secret Manager at runtime. Zero disk exposure."""
    client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": secret_path})
    return response.payload.data.decode("UTF-8").strip()


@functions_framework.http
def route_signal(request):
    """
    EvezArt Synapse Engine — HTTP endpoint bridging AI intent to GitHub execution.
    Deployed on Google Cloud Run Functions.
    """
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "3600",
    }

    if request.method == "OPTIONS":
        return ("", 204, cors_headers)

    try:
        payload = request.get_json(silent=True)
        if not payload:
            return (json.dumps({"error": "Payload missing or not JSON"}), 400, cors_headers)

        action = payload.get("action")
        target_repo = payload.get("repo", "EvezArt/evez-os")

        if not action:
            return (json.dumps({"error": "Parameter 'action' is required"}), 400, cors_headers)

        project_id = os.environ.get("GCP_PROJECT_ID")
        if not project_id:
            return (json.dumps({"error": "GCP_PROJECT_ID env var not set"}), 500, cors_headers)

        token = retrieve_token(project_id)
        git_client = Github(token)
        repo = git_client.get_repo(target_repo)

        if action == "audit_all":
            ref = payload.get("ref", "main")
            workflows = list(repo.get_workflows())
            target_wf = next(
                (w for w in workflows if "audit" in w.name.lower() or "security" in w.name.lower()),
                None
            )
            if not target_wf:
                return (json.dumps({"error": "No audit/security workflow found in repo"}), 404, cors_headers)
            target_wf.create_dispatch(ref=ref)
            return (json.dumps({"status": "dispatched", "workflow": target_wf.name, "ref": ref}), 200, cors_headers)

        elif action == "create_issue":
            title = payload.get("title", "Automated Synapse Entry")
            body = payload.get("body", "Delivered via EvezArt Synapse Engine.")
            labels = payload.get("labels", [])
            issue = repo.create_issue(title=title, body=body, labels=labels)
            return (json.dumps({"status": "created", "issue_number": issue.number, "url": issue.html_url}), 200, cors_headers)

        elif action == "list_issues":
            state = payload.get("state", "open")
            issues = list(repo.get_issues(state=state))[:20]
            return (json.dumps({
                "status": "ok",
                "issues": [{"number": i.number, "title": i.title, "state": i.state, "url": i.html_url} for i in issues]
            }), 200, cors_headers)

        elif action == "trigger_workflow":
            workflow_name = payload.get("workflow")
            ref = payload.get("ref", "main")
            inputs = payload.get("inputs", {})
            if not workflow_name:
                return (json.dumps({"error": "workflow parameter required"}), 400, cors_headers)
            workflows = list(repo.get_workflows())
            target_wf = next((w for w in workflows if workflow_name.lower() in w.name.lower() or workflow_name in w.path), None)
            if not target_wf:
                return (json.dumps({"error": f"Workflow '{workflow_name}' not found"}), 404, cors_headers)
            target_wf.create_dispatch(ref=ref, inputs=inputs)
            return (json.dumps({"status": "dispatched", "workflow": target_wf.name}), 200, cors_headers)

        elif action == "list_repos":
            repos_data = git_client.get_user("EvezArt").get_repos()
            return (json.dumps({
                "repos": [{"name": r.name, "private": r.private, "updated": str(r.updated_at)} for r in list(repos_data)[:30]]
            }), 200, cors_headers)

        elif action == "commit_file":
            path = payload.get("path")
            content = payload.get("content")
            message = payload.get("message", "Synapse commit")
            branch = payload.get("branch", "main")
            if not path or content is None:
                return (json.dumps({"error": "path and content required"}), 400, cors_headers)
            try:
                existing = repo.get_contents(path, ref=branch)
                repo.update_file(path, message, content, existing.sha, branch=branch)
                result = "updated"
            except GithubException:
                repo.create_file(path, message, content, branch=branch)
                result = "created"
            return (json.dumps({"status": result, "path": path}), 200, cors_headers)

        else:
            return (json.dumps({"error": f"Unknown action: {action}"}), 400, cors_headers)

    except GithubException as ge:
        return (json.dumps({"error": "GitHub API error", "detail": ge.data}), ge.status, cors_headers)
    except Exception as e:
        return (json.dumps({"error": "Execution exception", "detail": str(e)}), 500, cors_headers)
