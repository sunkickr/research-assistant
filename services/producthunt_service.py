import requests
from typing import List, Optional
from models.data_models import RedditThread, RedditComment


PH_API_URL = "https://api.producthunt.com/v2/api/graphql"


class ProductHuntService:
    """Discovers Product Hunt posts and collects comments via the GraphQL v2 API."""

    def __init__(self, api_token: str = ""):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    @property
    def available(self) -> bool:
        return bool(self.api_token)

    def _graphql(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query. Returns the 'data' dict or empty dict on error."""
        if not self.available:
            return {}
        try:
            resp = requests.post(
                PH_API_URL,
                json={"query": query, "variables": variables or {}},
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("data", {})
        except Exception:
            return {}

    def search_posts(
        self, product_name: str, max_results: int = 5
    ) -> List[RedditThread]:
        """Search Product Hunt posts by product name. Returns RedditThread objects."""
        if not self.available:
            return []

        query = """
        query SearchPosts($query: String!, $first: Int!) {
            posts(query: $query, first: $first, order: RANKING) {
                edges {
                    node {
                        id
                        name
                        slug
                        tagline
                        description
                        votesCount
                        commentsCount
                        createdAt
                        url
                        website
                        user { username }
                    }
                }
            }
        }
        """
        data = self._graphql(query, {"query": product_name, "first": max_results})
        posts = data.get("posts", {}).get("edges", [])

        threads = []
        for edge in posts:
            node = edge.get("node", {})
            slug = node.get("slug", "")
            if not slug:
                continue

            ph_id = node.get("id", "")
            created_at = node.get("createdAt", "")
            # Parse ISO timestamp to epoch
            created_utc = 0.0
            if created_at:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    created_utc = dt.timestamp()
                except Exception:
                    pass

            username = (node.get("user") or {}).get("username", "")
            description = node.get("description") or node.get("tagline") or ""

            threads.append(
                RedditThread(
                    id=f"ph_{slug}",
                    title=node.get("name", slug),
                    subreddit="Product Hunt",
                    score=node.get("votesCount") or 0,
                    num_comments=node.get("commentsCount") or 0,
                    url=node.get("website") or node.get("url") or f"https://www.producthunt.com/posts/{slug}",
                    permalink=f"https://www.producthunt.com/posts/{slug}",
                    selftext=description[:2000],
                    created_utc=created_utc,
                    author=username,
                    source="producthunt",
                )
            )

        return threads

    def collect_comments(
        self, thread_id: str, max_comments: int = 100
    ) -> List[RedditComment]:
        """
        Fetch comments for a Product Hunt post.
        thread_id should be 'ph_{slug}' format.
        """
        if not self.available:
            return []

        slug = thread_id.replace("ph_", "", 1)

        query = """
        query PostComments($slug: String!, $first: Int!) {
            post(slug: $slug) {
                comments(first: $first, order: VOTES) {
                    edges {
                        node {
                            id
                            body
                            votesCount
                            createdAt
                            user { username }
                            replies(first: 20) {
                                edges {
                                    node {
                                        id
                                        body
                                        votesCount
                                        createdAt
                                        user { username }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        data = self._graphql(query, {"slug": slug, "first": min(max_comments, 50)})
        post = data.get("post")
        if not post:
            return []

        edges = post.get("comments", {}).get("edges", [])
        comments = []

        for edge in edges:
            node = edge.get("node", {})
            comment = self._node_to_comment(node, thread_id, depth=0)
            if comment:
                comments.append(comment)

            # Flatten replies
            for reply_edge in (node.get("replies") or {}).get("edges", []):
                reply_node = reply_edge.get("node", {})
                reply = self._node_to_comment(reply_node, thread_id, depth=1)
                if reply:
                    comments.append(reply)

        comments.sort(key=lambda c: c.score, reverse=True)
        return comments[:max_comments]

    def _node_to_comment(
        self, node: dict, thread_id: str, depth: int
    ) -> Optional[RedditComment]:
        """Convert a GraphQL comment node to a RedditComment."""
        body = node.get("body", "").strip()
        if not body:
            return None

        comment_id = node.get("id", "")
        username = (node.get("user") or {}).get("username", "")
        created_at = node.get("createdAt", "")
        created_utc = 0.0
        if created_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                created_utc = dt.timestamp()
            except Exception:
                pass

        slug = thread_id.replace("ph_", "", 1)
        return RedditComment(
            id=f"ph_c{comment_id}",
            thread_id=thread_id,
            author=username or "Anonymous",
            body=body,
            score=node.get("votesCount") or 0,
            created_utc=created_utc,
            depth=depth,
            permalink=f"https://www.producthunt.com/posts/{slug}",
            source="producthunt",
        )
