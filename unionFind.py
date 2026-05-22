import sys
import json
from collections import defaultdict
import heapq


class UnionFind:
    def __init__(self):
        self.parents = {}
        self.rank = {}

    def add(self, x):
        if x not in self.parents:
            self.parents[x] = x
            self.rank[x] = 1

    def find(self, val):
        self.add(val)
        cur = val
        while cur != self.parents[cur]:
            self.parents[cur] = self.parents[self.parents[cur]]
            cur = self.parents[cur]
        return cur

    def union(self, n1, n2):
        self.add(n1)
        self.add(n2)
        p1, p2 = self.find(n1), self.find(n2)
        if p1 == p2:
            return False
        if self.rank[p1] > self.rank[p2]:
            self.parents[p2] = p1
            self.rank[p1] += self.rank[p2]
        else:
            self.parents[p1] = p2
            self.rank[p2] += self.rank[p1]
        return True


def compute_top_k_from_adjacency(adj, asset_type_token=None, k=3):
    uf = UnionFind()
    # adj expected as dict: node -> list(neighbors)
    for node, neighbors in adj.items():
        uf.add(node)
        for nb in neighbors:
            uf.union(node, nb)

    groups = defaultdict(set)
    for asset in uf.parents:
        root = uf.find(asset)
        groups[root].add(asset)

    heap = []
    token = asset_type_token
    for root, assets in groups.items():
        # find a representative 'main' asset that matches token if possible
        main_asset = None
        for a in assets:
            if token and token.lower() in a.lower():
                main_asset = a
                break
        if not main_asset:
            # fallback to root or any asset
            main_asset = root
        heapq.heappush(heap, (-len(assets), main_asset, list(assets)))

    result = []
    for _ in range(min(k, len(heap))):
        size_neg, main, members = heapq.heappop(heap)
        result.append({"main": main, "affected": -size_neg, "members": members})

    return result


def main():
    # read stdin
    try:
        raw = sys.stdin.read()
        if raw and raw.strip():
            payload = json.loads(raw)
            if "nodes" in payload:
                raw_adj = payload["nodes"]
                assetType = payload.get("assetType")
                k = int(payload.get("k", 3))
                # normalize adjacency input: accept dict, list-of-clusters (dicts), or list-of-lists
                adj = {}
                if isinstance(raw_adj, dict):
                    adj = raw_adj
                elif isinstance(raw_adj, list):
                    # try cluster-of-dicts format (wmaintree-style)
                    if len(raw_adj) > 0 and isinstance(raw_adj[0], dict):
                        tmp = {}
                        for cluster in raw_adj:
                            nodes = cluster.values()
                            # nodes = [
                            #     cluster.get("reg assetid"),
                            #     cluster.get("mxu assetid"),
                            #     cluster.get("meter assetid"),
                            #     cluster.get("mbox assetid"),
                            #     cluster.get("wsl assetid"),
                            #     cluster.get("main assetid"),
                            # ]
                            nodes = [n for n in nodes if n]
                            for i in range(len(nodes)):
                                tmp.setdefault(nodes[i], set())
                                for j in range(i + 1, len(nodes)):
                                    tmp[nodes[i]].add(nodes[j])
                                    tmp.setdefault(nodes[j], set()).add(nodes[i])
                        adj = {k: list(v) for k, v in tmp.items()}
                    else:
                        # assume list-of-sequences where each element is a cluster/list of node ids
                        tmp = {}
                        for cluster in raw_adj:
                            if not cluster:
                                continue
                            if isinstance(cluster, (list, tuple, set)):
                                nodes = list(cluster)
                            elif isinstance(cluster, str):
                                nodes = [cluster]
                            else:
                                # unknown element type; skip
                                continue
                            for i in range(len(nodes)):
                                tmp.setdefault(nodes[i], set())
                                for j in range(i + 1, len(nodes)):
                                    tmp[nodes[i]].add(nodes[j])
                                    tmp.setdefault(nodes[j], set()).add(nodes[i])
                        adj = {k: list(v) for k, v in tmp.items()}
                else:
                    # unsupported type - treat as empty
                    adj = {}

                # special-case mapping
                token = assetType
                if assetType and assetType.lower() == "wmain":
                    token = "WDMAI"

                result = compute_top_k_from_adjacency(adj, asset_type_token=token, k=k)
                # emit human-readable lines first (one per print) then the JSON
                human_lines = []
                for entry in result[:k]:
                    human_lines.append(str(entry.get("main")))
                    human_lines.append(f"Affected: {entry.get('affected')}")
                for line in human_lines:
                    print(line)
                print(json.dumps({"components": result}))
                return
        # fallback: try to read wmaintree.py file as before
        try:
            with open("wmaintree.py") as f:
                waterMainTree = json.loads(f.read())
        except Exception:
            print(json.dumps({"components": []}))
            return

        # build adjacency from old format (list of clusters)
        adj = {}
        for cluster in waterMainTree:
            nodes = cluster.values()
            # nodes = [
            #     cluster.get("reg assetid"),
            #     cluster.get("mxu assetid"),
            #     cluster.get("meter assetid"),
            #     cluster.get("mbox assetid"),
            #     cluster.get("wsl assetid"),
            #     cluster.get("main assetid"),
            # ]
            nodes = [n for n in nodes if n]
            for i in range(len(nodes)):
                adj.setdefault(nodes[i], set())
                for j in range(i + 1, len(nodes)):
                    adj[nodes[i]].add(nodes[j])
                    adj.setdefault(nodes[j], set()).add(nodes[i])
        # convert sets to lists
        adj = {k: list(v) for k, v in adj.items()}
        result = compute_top_k_from_adjacency(adj, asset_type_token="WDMAI", k=3)
        # emit human-readable lines then final JSON
        human_lines = []
        for entry in result[:3]:
            human_lines.append(str(entry.get("main")))
            human_lines.append(f"Affected: {entry.get('affected')}")
        for line in human_lines:
            print(line)
        print(json.dumps({"components": result}))

    except Exception as ex:
        # write human-readable errors to stderr so the controller can return them
        import traceback

        print("Exception during processing:\n", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # also emit an empty JSON so caller can try parsing if desired
        print(json.dumps({"components": []}))


if __name__ == "__main__":
    main()
