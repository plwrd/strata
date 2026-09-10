namespace Strata.Core.Schema;

/// <summary>
/// The knowledge loop's default areas (docs/target-architecture.md §2) — the slice
/// of <c>app/domain/schema.py</c> the workspace lifecycle needs.
/// </summary>
/// <remarks>
/// Folders, not walls: a new workspace gets these, and an existing one is never
/// forced into them.
/// </remarks>
public static class KnowledgeAreas
{
    public const string Inbox = "Inbox";
    public const string Knowledge = "Knowledge";
    public const string Reports = "Reports";
    public const string Templates = "Templates";

    public static readonly IReadOnlyList<string> All = [Inbox, Knowledge, Reports, Templates];
}
