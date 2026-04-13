# True Bootstrap Evaluation Report

## Configuration Status
- **LLM Provider**: ollama-openai
- **LLM Model**: qwen3.5:35b-a3b-think
- **Personality Role**: tars
- **Agent Configured**: Yes (System Prompts count/len approx: 2)
- **Knowledge Backend**: hybrid
- **Knowledge Store Configured**: Yes
- **MCP Servers Reg'ed**: 2 (['github', 'context7'])
- **Tools Discovered**: 56 (MCP: 28)
  - MCP Integrations: {'github': 26, 'context7': 2}
- **Skills Discovered**: 1
- **Session ID**: 12c6816d-bd5c-4154-8233-bca3af733420
- **Paths Resolved**: /Users/binle/workspace_genai/co-cli/.co-cli/memory, /Users/binle/.co-cli/library

## Dependencies Inspection
```python
deps = CoDeps(shell=<co_cli.tools.shell_backend.ShellBackend object at 0x10d247650>,
       config=Settings(llm=LlmSettings(api_key=None, provider='ollama-openai', host='http://localhost:11434', model='qwen3.5:35b-a3b-think', num_ctx=131072, ctx_warn_threshold=0.85, ctx_overflow_threshold=1.0), knowledge=KnowledgeSettings(search_backend='hybrid', embedding_provider='tei', embedding_model='embeddinggemma', embedding_dims=1024, cross_encoder_reranker_url='http://127.0.0.1:8282', llm_reranker=None, embed_api_url='http://127.0.0.1:8283', chunk_size=600, chunk_overlap=80), web=WebSettings(fetch_allowed_domains=[], fetch_blocked_domains=[], http_max_retries=2, http_backoff_base_seconds=1.0, http_backoff_max_seconds=8.0, http_jitter_ratio=0.2), subagent=SubagentSettings(scope_chars=120, max_requests_coder=10, max_requests_research=10, max_requests_analysis=8, max_requests_thinking=3), memory=MemorySettings(max_count=200, recall_half_life_days=30, auto_save_tags=['user', 'feedback', 'project', 'reference'], injection_max_chars=2000), shell=ShellSettings(max_timeout=600, safe_commands=['ls', 'tree', 'find', 'fd', 'cat', 'head', 'tail', 'grep', 'rg', 'ag', 'wc', 'sort', 'uniq', 'cut', 'tr', 'jq', 'echo', 'printf', 'pwd', 'whoami', 'hostname', 'uname', 'date', 'env', 'which', 'file', 'stat', 'id', 'du', 'df', 'git status', 'git diff', 'git log', 'git show', 'git branch', 'git tag', 'git blame']), obsidian_vault_path=None, brave_search_api_key='BSAQbTlNOYWwbFSNgtqGHHCsZFE8kKq', google_credentials_path=None, library_path=None, theme='light', reasoning_display='summary', personality='tars', tool_retries=3, doom_loop_threshold=3, max_reflections=3, mcp_servers={'github': MCPServerConfig(command='npx', url=None, args=['-y', '@modelcontextprotocol/server-github'], timeout=5, env={'GITHUB_PERSONAL_ACCESS_TOKEN': '***'}, approval='ask', prefix=None), 'context7': MCPServerConfig(command='npx', url=None, args=['-y', '@upstash/context7-mcp@latest'], timeout=5, env={}, approval='auto', prefix=None)}),
       tool_index={ 'append_memory': ToolInfo(name='append_memory',
                                              description='Append content to the end of an '
                                                          'existing memory file.',
                                              approval=True,
                                              source=<ToolSourceEnum.NATIVE: 'native'>,
                                              visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                              integration=None,
                                              max_result_size=50000),
                    'cancel_background_task': ToolInfo(name='cancel_background_task',
                                                       description='Cancel a running background '
                                                                   'task to stop wasted work '
                                                                   '(SIGTERM, then SIGKILL).',
                                                       approval=False,
                                                       source=<ToolSourceEnum.NATIVE: 'native'>,
                                                       visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                       integration=None,
                                                       max_result_size=50000),
                    'check_capabilities': ToolInfo(name='check_capabilities',
                                                   description='Return a summary of active '
                                                               'capabilities and integration '
                                                               'health.',
                                                   approval=False,
                                                   source=<ToolSourceEnum.NATIVE: 'native'>,
                                                   visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                                   integration=None,
                                                   max_result_size=50000),
                    'check_task_status': ToolInfo(name='check_task_status',
                                                  description='Check status and recent output of a '
                                                              'specific background task by ID.',
                                                  approval=False,
                                                  source=<ToolSourceEnum.NATIVE: 'native'>,
                                                  visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                  integration=None,
                                                  max_result_size=50000),
                    'context7_query-docs': ToolInfo(name='context7_query-docs',
                                                    description='Retrieves and queries up-to-date '
                                                                'documentation and code examples '
                                                                'from Context7 for any programming '
                                                                'library or framework.\n'
                                                                '\n'
                                                                "You must call 'Resolve Context7 "
                                                                "Library ID' tool first to obtain "
                                                                'the exact Context7-compatible '
                                                                'library ID required to use this '
                                                                'tool, UNLESS the user explicitly '
                                                                'provides a library ID in the '
                                                                "format '/org/project' or "
                                                                "'/org/project/version' in their "
                                                                'query.\n'
                                                                '\n'
                                                                'IMPORTANT: Do not call this tool '
                                                                'more than 3 times per question. '
                                                                'If you cannot find what you need '
                                                                'after 3 calls, use the best '
                                                                'information you have.',
                                                    approval=False,
                                                    source=<ToolSourceEnum.MCP: 'mcp'>,
                                                    visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                    integration='context7',
                                                    max_result_size=50000),
                    'context7_resolve-library-id': ToolInfo(name='context7_resolve-library-id',
                                                            description='Resolves a '
                                                                        'package/product name to a '
                                                                        'Context7-compatible '
                                                                        'library ID and returns '
                                                                        'matching libraries.\n'
                                                                        '\n'
                                                                        'You MUST call this '
                                                                        "function before 'Query "
                                                                        "Documentation' tool to "
                                                                        'obtain a valid '
                                                                        'Context7-compatible '
                                                                        'library ID UNLESS the '
                                                                        'user explicitly provides '
                                                                        'a library ID in the '
                                                                        "format '/org/project' or "
                                                                        "'/org/project/version' in "
                                                                        'their query.\n'
                                                                        '\n'
                                                                        'Each result includes:\n'
                                                                        '- Library ID: '
                                                                        'Context7-compatible '
                                                                        'identifier (format: '
                                                                        '/org/project)\n'
                                                                        '- Name: Library or '
                                                                        'package name\n'
                                                                        '- Description: Short '
                                                                        'summary\n'
                                                                        '- Code Snippets: Number '
                                                                        'of available code '
                                                                        'examples\n'
                                                                        '- Source Reputation: '
                                                                        'Authority indicator '
                                                                        '(High, Medium, Low, or '
                                                                        'Unknown)\n'
                                                                        '- Benchmark Score: '
                                                                        'Quality indicator (100 is '
                                                                        'the highest score)\n'
                                                                        '- Versions: List of '
                                                                        'versions if available. '
                                                                        'Use one of those versions '
                                                                        'if the user provides a '
                                                                        'version in their query. '
                                                                        'The format of the version '
                                                                        'is /org/project/version.\n'
                                                                        '\n'
                                                                        'For best results, select '
                                                                        'libraries based on name '
                                                                        'match, source reputation, '
                                                                        'snippet coverage, '
                                                                        'benchmark score, and '
                                                                        'relevance to your use '
                                                                        'case.\n'
                                                                        '\n'
                                                                        'Selection Process:\n'
                                                                        '1. Analyze the query to '
                                                                        'understand what '
                                                                        'library/package the user '
                                                                        'is looking for\n'
                                                                        '2. Return the most '
                                                                        'relevant match based on:\n'
                                                                        '- Name similarity to the '
                                                                        'query (exact matches '
                                                                        'prioritized)\n'
                                                                        '- Description relevance '
                                                                        "to the query's intent\n"
                                                                        '- Documentation coverage '
                                                                        '(prioritize libraries '
                                                                        'with higher Code Snippet '
                                                                        'counts)\n'
                                                                        '- Source reputation '
                                                                        '(consider libraries with '
                                                                        'High or Medium reputation '
                                                                        'more authoritative)\n'
                                                                        '- Benchmark Score: '
                                                                        'Quality indicator (100 is '
                                                                        'the highest score)\n'
                                                                        '\n'
                                                                        'Response Format:\n'
                                                                        '- Return the selected '
                                                                        'library ID in a clearly '
                                                                        'marked section\n'
                                                                        '- Provide a brief '
                                                                        'explanation for why this '
                                                                        'library was chosen\n'
                                                                        '- If multiple good '
                                                                        'matches exist, '
                                                                        'acknowledge this but '
                                                                        'proceed with the most '
                                                                        'relevant one\n'
                                                                        '- If no good matches '
                                                                        'exist, clearly state this '
                                                                        'and suggest query '
                                                                        'refinements\n'
                                                                        '\n'
                                                                        'For ambiguous queries, '
                                                                        'request clarification '
                                                                        'before proceeding with a '
                                                                        'best-guess match.\n'
                                                                        '\n'
                                                                        'IMPORTANT: Do not call '
                                                                        'this tool more than 3 '
                                                                        'times per question. If '
                                                                        'you cannot find what you '
                                                                        'need after 3 calls, use '
                                                                        'the best result you have.',
                                                            approval=False,
                                                            source=<ToolSourceEnum.MCP: 'mcp'>,
                                                            visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                            integration='context7',
                                                            max_result_size=50000),
                    'edit_file': ToolInfo(name='edit_file',
                                          description='Edit a file by replacing a specific search '
                                                      'string with a replacement.',
                                          approval=True,
                                          source=<ToolSourceEnum.NATIVE: 'native'>,
                                          visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                          integration=None,
                                          max_result_size=50000),
                    'find_in_files': ToolInfo(name='find_in_files',
                                              description='Search file contents by regex pattern '
                                                          'across the workspace.',
                                              approval=False,
                                              source=<ToolSourceEnum.NATIVE: 'native'>,
                                              visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                              integration=None,
                                              max_result_size=50000),
                    'github_add_issue_comment': ToolInfo(name='github_add_issue_comment',
                                                         description='Add a comment to an existing '
                                                                     'issue',
                                                         approval=True,
                                                         source=<ToolSourceEnum.MCP: 'mcp'>,
                                                         visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                         integration='github',
                                                         max_result_size=50000),
                    'github_create_branch': ToolInfo(name='github_create_branch',
                                                     description='Create a new branch in a GitHub '
                                                                 'repository',
                                                     approval=True,
                                                     source=<ToolSourceEnum.MCP: 'mcp'>,
                                                     visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                     integration='github',
                                                     max_result_size=50000),
                    'github_create_issue': ToolInfo(name='github_create_issue',
                                                    description='Create a new issue in a GitHub '
                                                                'repository',
                                                    approval=True,
                                                    source=<ToolSourceEnum.MCP: 'mcp'>,
                                                    visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                    integration='github',
                                                    max_result_size=50000),
                    'github_create_or_update_file': ToolInfo(name='github_create_or_update_file',
                                                             description='Create or update a '
                                                                         'single file in a GitHub '
                                                                         'repository',
                                                             approval=True,
                                                             source=<ToolSourceEnum.MCP: 'mcp'>,
                                                             visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                             integration='github',
                                                             max_result_size=50000),
                    'github_create_pull_request': ToolInfo(name='github_create_pull_request',
                                                           description='Create a new pull request '
                                                                       'in a GitHub repository',
                                                           approval=True,
                                                           source=<ToolSourceEnum.MCP: 'mcp'>,
                                                           visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                           integration='github',
                                                           max_result_size=50000),
                    'github_create_pull_request_review': ToolInfo(name='github_create_pull_request_review',
                                                                  description='Create a review on '
                                                                              'a pull request',
                                                                  approval=True,
                                                                  source=<ToolSourceEnum.MCP: 'mcp'>,
                                                                  visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                                  integration='github',
                                                                  max_result_size=50000),
                    'github_create_repository': ToolInfo(name='github_create_repository',
                                                         description='Create a new GitHub '
                                                                     'repository in your account',
                                                         approval=True,
                                                         source=<ToolSourceEnum.MCP: 'mcp'>,
                                                         visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                         integration='github',
                                                         max_result_size=50000),
                    'github_fork_repository': ToolInfo(name='github_fork_repository',
                                                       description='Fork a GitHub repository to '
                                                                   'your account or specified '
                                                                   'organization',
                                                       approval=True,
                                                       source=<ToolSourceEnum.MCP: 'mcp'>,
                                                       visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                       integration='github',
                                                       max_result_size=50000),
                    'github_get_file_contents': ToolInfo(name='github_get_file_contents',
                                                         description='Get the contents of a file '
                                                                     'or directory from a GitHub '
                                                                     'repository',
                                                         approval=True,
                                                         source=<ToolSourceEnum.MCP: 'mcp'>,
                                                         visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                         integration='github',
                                                         max_result_size=50000),
                    'github_get_issue': ToolInfo(name='github_get_issue',
                                                 description='Get details of a specific issue in a '
                                                             'GitHub repository.',
                                                 approval=True,
                                                 source=<ToolSourceEnum.MCP: 'mcp'>,
                                                 visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                 integration='github',
                                                 max_result_size=50000),
                    'github_get_pull_request': ToolInfo(name='github_get_pull_request',
                                                        description='Get details of a specific '
                                                                    'pull request',
                                                        approval=True,
                                                        source=<ToolSourceEnum.MCP: 'mcp'>,
                                                        visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                        integration='github',
                                                        max_result_size=50000),
                    'github_get_pull_request_comments': ToolInfo(name='github_get_pull_request_comments',
                                                                 description='Get the review '
                                                                             'comments on a pull '
                                                                             'request',
                                                                 approval=True,
                                                                 source=<ToolSourceEnum.MCP: 'mcp'>,
                                                                 visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                                 integration='github',
                                                                 max_result_size=50000),
                    'github_get_pull_request_files': ToolInfo(name='github_get_pull_request_files',
                                                              description='Get the list of files '
                                                                          'changed in a pull '
                                                                          'request',
                                                              approval=True,
                                                              source=<ToolSourceEnum.MCP: 'mcp'>,
                                                              visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                              integration='github',
                                                              max_result_size=50000),
                    'github_get_pull_request_reviews': ToolInfo(name='github_get_pull_request_reviews',
                                                                description='Get the reviews on a '
                                                                            'pull request',
                                                                approval=True,
                                                                source=<ToolSourceEnum.MCP: 'mcp'>,
                                                                visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                                integration='github',
                                                                max_result_size=50000),
                    'github_get_pull_request_status': ToolInfo(name='github_get_pull_request_status',
                                                               description='Get the combined '
                                                                           'status of all status '
                                                                           'checks for a pull '
                                                                           'request',
                                                               approval=True,
                                                               source=<ToolSourceEnum.MCP: 'mcp'>,
                                                               visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                               integration='github',
                                                               max_result_size=50000),
                    'github_list_commits': ToolInfo(name='github_list_commits',
                                                    description='Get list of commits of a branch '
                                                                'in a GitHub repository',
                                                    approval=True,
                                                    source=<ToolSourceEnum.MCP: 'mcp'>,
                                                    visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                    integration='github',
                                                    max_result_size=50000),
                    'github_list_issues': ToolInfo(name='github_list_issues',
                                                   description='List issues in a GitHub repository '
                                                               'with filtering options',
                                                   approval=True,
                                                   source=<ToolSourceEnum.MCP: 'mcp'>,
                                                   visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                   integration='github',
                                                   max_result_size=50000),
                    'github_list_pull_requests': ToolInfo(name='github_list_pull_requests',
                                                          description='List and filter repository '
                                                                      'pull requests',
                                                          approval=True,
                                                          source=<ToolSourceEnum.MCP: 'mcp'>,
                                                          visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                          integration='github',
                                                          max_result_size=50000),
                    'github_merge_pull_request': ToolInfo(name='github_merge_pull_request',
                                                          description='Merge a pull request',
                                                          approval=True,
                                                          source=<ToolSourceEnum.MCP: 'mcp'>,
                                                          visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                          integration='github',
                                                          max_result_size=50000),
                    'github_push_files': ToolInfo(name='github_push_files',
                                                  description='Push multiple files to a GitHub '
                                                              'repository in a single commit',
                                                  approval=True,
                                                  source=<ToolSourceEnum.MCP: 'mcp'>,
                                                  visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                  integration='github',
                                                  max_result_size=50000),
                    'github_search_code': ToolInfo(name='github_search_code',
                                                   description='Search for code across GitHub '
                                                               'repositories',
                                                   approval=True,
                                                   source=<ToolSourceEnum.MCP: 'mcp'>,
                                                   visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                   integration='github',
                                                   max_result_size=50000),
                    'github_search_issues': ToolInfo(name='github_search_issues',
                                                     description='Search for issues and pull '
                                                                 'requests across GitHub '
                                                                 'repositories',
                                                     approval=True,
                                                     source=<ToolSourceEnum.MCP: 'mcp'>,
                                                     visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                     integration='github',
                                                     max_result_size=50000),
                    'github_search_repositories': ToolInfo(name='github_search_repositories',
                                                           description='Search for GitHub '
                                                                       'repositories',
                                                           approval=True,
                                                           source=<ToolSourceEnum.MCP: 'mcp'>,
                                                           visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                           integration='github',
                                                           max_result_size=50000),
                    'github_search_users': ToolInfo(name='github_search_users',
                                                    description='Search for users on GitHub',
                                                    approval=True,
                                                    source=<ToolSourceEnum.MCP: 'mcp'>,
                                                    visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                    integration='github',
                                                    max_result_size=50000),
                    'github_update_issue': ToolInfo(name='github_update_issue',
                                                    description='Update an existing issue in a '
                                                                'GitHub repository',
                                                    approval=True,
                                                    source=<ToolSourceEnum.MCP: 'mcp'>,
                                                    visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                    integration='github',
                                                    max_result_size=50000),
                    'github_update_pull_request_branch': ToolInfo(name='github_update_pull_request_branch',
                                                                  description='Update a pull '
                                                                              'request branch with '
                                                                              'the latest changes '
                                                                              'from the base '
                                                                              'branch',
                                                                  approval=True,
                                                                  source=<ToolSourceEnum.MCP: 'mcp'>,
                                                                  visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                                  integration='github',
                                                                  max_result_size=50000),
                    'list_background_tasks': ToolInfo(name='list_background_tasks',
                                                      description='List all background tasks to '
                                                                  'discover active work or recover '
                                                                  'a task ID.',
                                                      approval=False,
                                                      source=<ToolSourceEnum.NATIVE: 'native'>,
                                                      visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                      integration=None,
                                                      max_result_size=50000),
                    'list_directory': ToolInfo(name='list_directory',
                                               description='List directory contents or find files '
                                                           'by name pattern (glob).',
                                               approval=False,
                                               source=<ToolSourceEnum.NATIVE: 'native'>,
                                               visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                               integration=None,
                                               max_result_size=50000),
                    'list_memories': ToolInfo(name='list_memories',
                                              description='List saved memories with IDs, dates, '
                                                          'tags, and one-line summaries.',
                                              approval=False,
                                              source=<ToolSourceEnum.NATIVE: 'native'>,
                                              visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                              integration=None,
                                              max_result_size=50000),
                    'read_article': ToolInfo(name='read_article',
                                             description='Load the full markdown body of a saved '
                                                         'article on demand.',
                                             approval=False,
                                             source=<ToolSourceEnum.NATIVE: 'native'>,
                                             visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                             integration=None,
                                             max_result_size=50000),
                    'read_file': ToolInfo(name='read_file',
                                          description="Read a file's contents for targeted "
                                                      'inspection, with optional line range.',
                                          approval=False,
                                          source=<ToolSourceEnum.NATIVE: 'native'>,
                                          visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                          integration=None,
                                          max_result_size=80000),
                    'read_todos': ToolInfo(name='read_todos',
                                           description='Read the current session todo list to '
                                                       'verify progress and completeness.',
                                           approval=False,
                                           source=<ToolSourceEnum.NATIVE: 'native'>,
                                           visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                           integration=None,
                                           max_result_size=50000),
                    'run_analysis_subagent': ToolInfo(name='run_analysis_subagent',
                                                      description='Delegate knowledge-base '
                                                                  'analysis to a sub-agent with '
                                                                  'memory and Drive search.',
                                                      approval=False,
                                                      source=<ToolSourceEnum.NATIVE: 'native'>,
                                                      visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                      integration=None,
                                                      max_result_size=50000),
                    'run_coding_subagent': ToolInfo(name='run_coding_subagent',
                                                    description='Delegate codebase analysis to a '
                                                                'read-only coder sub-agent with '
                                                                'file tools.',
                                                    approval=False,
                                                    source=<ToolSourceEnum.NATIVE: 'native'>,
                                                    visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                    integration=None,
                                                    max_result_size=50000),
                    'run_reasoning_subagent': ToolInfo(name='run_reasoning_subagent',
                                                       description='Delegate structured reasoning '
                                                                   'to a tool-free thinking '
                                                                   'sub-agent.',
                                                       approval=False,
                                                       source=<ToolSourceEnum.NATIVE: 'native'>,
                                                       visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                       integration=None,
                                                       max_result_size=50000),
                    'run_research_subagent': ToolInfo(name='run_research_subagent',
                                                      description='Delegate web research to a '
                                                                  'search-and-fetch sub-agent with '
                                                                  'web tools.',
                                                      approval=False,
                                                      source=<ToolSourceEnum.NATIVE: 'native'>,
                                                      visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                      integration=None,
                                                      max_result_size=50000),
                    'run_shell_command': ToolInfo(name='run_shell_command',
                                                  description='Execute a shell command and return '
                                                              'combined stdout + stderr as text.',
                                                  approval=False,
                                                  source=<ToolSourceEnum.NATIVE: 'native'>,
                                                  visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                                  integration=None,
                                                  max_result_size=30000),
                    'save_article': ToolInfo(name='save_article',
                                             description='Save an article from external reference '
                                                         'material for long-term retrieval.',
                                             approval=True,
                                             source=<ToolSourceEnum.NATIVE: 'native'>,
                                             visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                             integration=None,
                                             max_result_size=50000),
                    'save_memory': ToolInfo(name='save_memory',
                                            description='Saves a memory. If a near-duplicate '
                                                        'exists, the existing memory is',
                                            approval=True,
                                            source=<ToolSourceEnum.NATIVE: 'native'>,
                                            visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                            integration=None,
                                            max_result_size=50000),
                    'search_articles': ToolInfo(name='search_articles',
                                                description='Search saved articles by keyword and '
                                                            'return summary index only',
                                                approval=False,
                                                source=<ToolSourceEnum.NATIVE: 'native'>,
                                                visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                                integration=None,
                                                max_result_size=50000),
                    'search_knowledge': ToolInfo(name='search_knowledge',
                                                 description='Primary cross-source knowledge '
                                                             'search — use this when the source is '
                                                             'unknown',
                                                 approval=False,
                                                 source=<ToolSourceEnum.NATIVE: 'native'>,
                                                 visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                                 integration=None,
                                                 max_result_size=50000),
                    'search_memories': ToolInfo(name='search_memories',
                                                description='Dedicated semantic search over saved '
                                                            'memories. Use this to look up',
                                                approval=False,
                                                source=<ToolSourceEnum.NATIVE: 'native'>,
                                                visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                                integration=None,
                                                max_result_size=50000),
                    'start_background_task': ToolInfo(name='start_background_task',
                                                      description='Start a long-running background '
                                                                  'shell command without blocking '
                                                                  'the chat.',
                                                      approval=True,
                                                      source=<ToolSourceEnum.NATIVE: 'native'>,
                                                      visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                                      integration=None,
                                                      max_result_size=50000),
                    'update_memory': ToolInfo(name='update_memory',
                                              description='Surgically replace a specific passage '
                                                          'in a memory file without rewriting',
                                              approval=True,
                                              source=<ToolSourceEnum.NATIVE: 'native'>,
                                              visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                              integration=None,
                                              max_result_size=50000),
                    'web_fetch': ToolInfo(name='web_fetch',
                                          description='Fetch a web page and return its content as '
                                                      'readable markdown text.',
                                          approval=False,
                                          source=<ToolSourceEnum.NATIVE: 'native'>,
                                          visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                          integration=None,
                                          max_result_size=50000),
                    'web_search': ToolInfo(name='web_search',
                                           description='Search the web for current or external '
                                                       'information via Brave Search.',
                                           approval=False,
                                           source=<ToolSourceEnum.NATIVE: 'native'>,
                                           visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                           integration=None,
                                           max_result_size=50000),
                    'write_file': ToolInfo(name='write_file',
                                           description='Write content to a new file or completely '
                                                       'rewrite an existing file.',
                                           approval=True,
                                           source=<ToolSourceEnum.NATIVE: 'native'>,
                                           visibility=<VisibilityPolicyEnum.DEFERRED: 'deferred'>,
                                           integration=None,
                                           max_result_size=50000),
                    'write_todos': ToolInfo(name='write_todos',
                                            description='Replace the session todo list for '
                                                        'tracking multi-step work.',
                                            approval=False,
                                            source=<ToolSourceEnum.NATIVE: 'native'>,
                                            visibility=<VisibilityPolicyEnum.ALWAYS: 'always'>,
                                            integration=None,
                                            max_result_size=50000)},
       skill_commands={ 'doctor': SkillConfig(name='doctor',
                                              description='Structured troubleshooting workflow — '
                                                          'diagnose system health and identify '
                                                          'degraded conditions',
                                              body='Run `check_capabilities` to get the full '
                                                   'runtime picture: capabilities, session state, '
                                                   'findings, and active fallbacks.\n'
                                                   '\n'
                                                   'Review the result against any prior context in '
                                                   'this conversation (what the user was trying to '
                                                   'do, what failed). Identify the most relevant '
                                                   'degraded or blocking condition.\n'
                                                   '\n'
                                                   'If more information is needed to diagnose, run '
                                                   'one targeted read-only follow-up (e.g. '
                                                   '`read_file` to inspect a credential or config '
                                                   "path, `web_search` to look up a tool's "
                                                   'requirements). Do not call '
                                                   '`check_capabilities` a second time.\n'
                                                   '\n'
                                                   'Respond with this exact structure:\n'
                                                   '\n'
                                                   '**Likely issue:** What is wrong or degraded — '
                                                   'be specific (e.g. "Gemini API key not set", '
                                                   '"knowledge index offline — grep fallback '
                                                   'active", "MCP server `notes` binary not '
                                                   'found").\n'
                                                   '\n'
                                                   '**What still works:** List capabilities that '
                                                   'are functioning normally and relevant to the '
                                                   "user's context.\n"
                                                   '\n'
                                                   '**Active fallback:** Any degraded-mode '
                                                   'operation currently in effect (from the '
                                                   '`fallbacks` list). If none, say "none".\n'
                                                   '\n'
                                                   '**What Co should do next:** One concrete next '
                                                   'step — either a config fix the user can apply, '
                                                   'or an alternative approach Co can take right '
                                                   'now.\n'
                                                   '\n'
                                                   'Keep the diagnosis concise and contextual. '
                                                   'Doctor recommends — does not repair.',
                                              argument_hint='',
                                              user_invocable=True,
                                              disable_model_invocation=False,
                                              requires={},
                                              skill_env={})},
       session=CoSessionState(google_creds_resolved=False,
                              session_approval_rules=[],
                              drive_page_tokens={},
                              session_todos=[],
                              session_id='12c6816d-bd5c-4154-8233-bca3af733420',
                              memory_recall_state=MemoryRecallState(recall_count=0,
                                                                    model_request_count=0,
                                                                    last_recall_user_turn=0),
                              background_tasks={}),
       runtime=CoRuntimeState(compaction_failure_count=0,
                              turn_usage=None,
                              active_skill_name=None,
                              resume_tool_names=None),
       workspace_root=PosixPath('/Users/binle/workspace_genai/co-cli'),
       obsidian_vault_path=None,
       memory_dir=PosixPath('/Users/binle/workspace_genai/co-cli/.co-cli/memory'),
       skills_dir=PosixPath('/Users/binle/workspace_genai/co-cli/.co-cli/skills'),
       user_skills_dir=PosixPath('/Users/binle/.co-cli/skills'),
       library_dir=PosixPath('/Users/binle/.co-cli/library'),
       knowledge_db_path=PosixPath('/Users/binle/.co-cli/co-cli-search.db'),
       sessions_dir=PosixPath('/Users/binle/workspace_genai/co-cli/.co-cli/sessions'),
       tool_results_dir=PosixPath('/Users/binle/workspace_genai/co-cli/.co-cli/tool-results'),
       degradations={})
```

## Execution Log & Debug Details
- **STATUS:** Starting true create_deps()
- **STATUS:**   Knowledge synced — 0 item(s) (hybrid)
- **STATUS:** create_deps() succeeded.
- **STATUS:** Building agent with personality: 'tars'
- **STATUS:** build_agent() succeeded.
- **STATUS:** Restoring session.
- **STATUS:**   Session restored — 12c6816d...
- **DEBUG:** INFO [co_cli.bootstrap.core] Ollama runtime num_ctx=131072 differs from config llm.num_ctx=262144 — using runtime value
