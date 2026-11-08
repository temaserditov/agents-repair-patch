"""Small non-blocking facade over Hermes 0.23 Kanban; no parallel scheduler."""
import json
import os
from pathlib import Path
import re

PROFILES = {'marketer', 'copywriter', 'designer', 'editor', 'tech', 'assistant', 'sales'}


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', value):
        raise ValueError('Use an id of 1–48 lowercase letters, digits or hyphens')
    return value


def text(value, limit=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('A non-empty bounded text is required')
    return value.strip()


def owner_only():
    # Descendants cannot escape Hermes' board/run ownership fences.
    from agent.delegation_context import is_delegated_child_context
    if os.environ.get('HERMES_KANBAN_TASK') or is_delegated_child_context():
        raise PermissionError('Task workers use kanban_show / kanban_complete; only the initiating agent creates workflows')


def project_board(project_id):
    return 'team-' + slug(project_id)


def team_project_create(args, **kwargs):
    owner_only()
    from hermes_cli import kanban_db as kb
    board = project_board(args['project_id'])
    title = text(args['title'], 200)
    workspace = Path(text(args['workspace'], 2048))
    if not workspace.is_absolute() or not workspace.is_dir():
        raise ValueError('Workspace must be an existing absolute directory shared by the agents')
    if kb.board_exists(board):
        previous = kb.read_board_metadata(board)
        if previous.get('name') != title or previous.get('default_workdir') != str(workspace):
            raise ValueError('Project id already belongs to another workspace/title')
    else:
        kb.create_board(board, name=title, default_workdir=str(workspace))
    return {'project_id': args['project_id'], 'board': board, 'workspace': str(workspace)}


def team_workflow_create(args, **kwargs):
    owner_only()
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli.profiles import profile_exists
    board = project_board(args['project_id'])
    workflow = slug(args['workflow_id'])
    steps = args['steps']
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError('Use 1–20 ordered steps')
    if not kb.board_exists(board):
        raise ValueError('Create the project first')
    workspace = kb.read_board_metadata(board).get('default_workdir')
    if not workspace or not Path(workspace).is_dir():
        raise ValueError('Project workspace is unavailable')
    checked = []
    for step in steps:
        profile = step['profile']
        if profile not in PROFILES or not profile_exists(profile):
            raise ValueError('Requested profile is not installed')
        checked.append((profile, text(step['title'], 200), text(step['brief'])))
    # Validate the entire request before writes. A retry reuses cards by their
    # native idempotency key; changed briefs under the same id are refused.
    with connect_closing(board=board) as conn, kb.write_txn(conn):
        tasks = kb.list_tasks(conn)
        prefix = 'infobiz-team:' + workflow + ':'
        existing = [t for t in tasks if (t.idempotency_key or '').startswith(prefix)]
        if len(existing) > len(checked):
            raise ValueError('Workflow id already has a different step count')
        for i, (profile, title, brief) in enumerate(checked):
            old = next((t for t in existing if t.idempotency_key == prefix + str(i)), None)
            if old and (old.assignee, old.title, old.body) != (profile, title, brief):
                raise ValueError('Workflow id already has different steps')
        parent = []
        ids = []
        for i, (profile, title, brief) in enumerate(checked):
            task = kb.create_task(conn, title=title, body=brief, assignee=profile,
                workspace_kind='dir', workspace_path=workspace, parents=parent,
                idempotency_key=prefix + str(i), board=board)
            ids.append(task)
            parent = [task]
    # Do NOT wait for a worker, invoke delegate_task, occupy a gateway session,
    # claim a task, or start a second dispatcher. The built-in gateway ticks all
    # boards and kanban_complete releases the worker claim before the next step.
    return {'board': board, 'task_ids': ids, 'status': 'queued',
        'next': 'Задачи в очереди. Заверши текущий ответ; результат проверяй через team_project_status. Не жди следующий профиль в занятой сессии.'}


def team_project_status(args, **kwargs):
    owner_only()
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    board = project_board(args['project_id'])
    if not kb.board_exists(board):
        raise ValueError('Project does not exist')
    with connect_closing(board=board) as conn:
        tasks = [{'id': t.id, 'profile': t.assignee, 'title': t.title,
                  'status': t.status, 'result': t.result} for t in kb.list_tasks(conn)]
    return {'board': board, 'tasks': tasks, 'complete': bool(tasks) and all(t['status'] == 'done' for t in tasks)}


def register(ctx):
    definitions = [
        ('team_project_create', team_project_create, 'Создать проект с общей папкой результатов.', {
            'project_id': {'type': 'string'}, 'title': {'type': 'string'}, 'workspace': {'type': 'string'}}),
        ('team_workflow_create', team_workflow_create, 'Поставить последовательные задачи профилям в Kanban и сразу освободить текущую сессию.', {
            'project_id': {'type': 'string'}, 'workflow_id': {'type': 'string'}, 'steps': {'type': 'array', 'minItems': 1, 'maxItems': 20,
                'items': {'type': 'object', 'properties': {k: {'type': 'string'} for k in ['profile', 'title', 'brief']},
                          'required': ['profile', 'title', 'brief'], 'additionalProperties': False}}}),
        ('team_project_status', team_project_status, 'Проверить готовые задачи и результаты проекта.', {'project_id': {'type': 'string'}}),
    ]
    def handler(function):
        def invoke(args, **kwargs):
            try:
                return json.dumps(function(args, **kwargs), ensure_ascii=False)
            except (ValueError, KeyError, TypeError, PermissionError) as error:
                return json.dumps({'error': str(error)}, ensure_ascii=False)
        return invoke
    def available():
        from agent.delegation_context import is_delegated_child_context
        return not os.environ.get('HERMES_KANBAN_TASK') and not is_delegated_child_context()
    for name, function, description, properties in definitions:
        ctx.register_tool(name=name, toolset='kanban', description=description,
            schema={'name': name, 'description': description, 'parameters': {'type': 'object',
                'properties': properties, 'required': list(properties), 'additionalProperties': False}},
            handler=handler(function), check_fn=available)
