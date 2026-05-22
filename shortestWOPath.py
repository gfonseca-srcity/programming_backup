import math
from functools import wraps
from geopy.geocoders import Nominatim
from geopy import distance
from dotenv import load_dotenv
import pyodbc
import os
import re
import heapq
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import concurrent.futures
import itertools
import threading
import sys

try:
    import osmnx as ox
    import networkx as nx
except Exception:
    ox = None
    nx = None

load_dotenv()


class SQL_Executor:
    SERVER = os.getenv("SERVER")
    DATABASE = os.getenv("DATABASE")
    WINUSER = os.getenv("WINUSER")
    WINPSWD = os.getenv("WINPSWD")
    SERVER = "CWPRODSQL"
    DATABASE = "CITYWORKS"

    def __init__(self):
        self.connection_string = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={self.SERVER};DATABASE={self.DATABASE};UID={self.WINUSER};PWD={self.WINPSWD};Trusted_Connection=yes;"
        self.workorders = []
        self.woids = set()
        self.wodistances = {}
        self.matrix_lock = threading.Lock()
        self.seen_edges = set()
        # print(self.connection_string)

    def getOpenWOsById(self, woids):
        with pyodbc.connect(self.connection_string) as conn:
            cursor = conn.cursor()
            woids = list(map(lambda o: "'" + str(o) + "'", woids))
            query = (
                f"SELECT TOP 10 WORKORDERID, SUBMITTO, DESCRIPTION, STATUS, WOADDRESS, LOCATION, WOXCOORDINATE, WOYCOORDINATE FROM AZTECA.WORKORDER \
                        WHERE WORKORDERID in ({",".join(woids)}) \
                            AND WOXCOORDINATE IS NOT NULL AND WOYCOORDINATE IS NOT NULL  \
                        ORDER BY INITIATEDATE DESC;"
            )
            print(query, file=sys.stderr)
            cursor.execute(query)
            rows = cursor.fetchall()
            print(rows, file=sys.stderr)
            for row in rows:
                curWO = {}
                WORKORDERID = row[0]
                curWO["WORKORDERID"] = WORKORDERID
                curWO["SUBMITTO"] = row[1]
                curWO["DESCRIPTION"] = row[2]
                curWO["STATUS"] = row[3]
                # curWO['ENTITIES'] = []
                curWO["WOADDRESS"] = row[4]
                # if re.search(r"^\s*0", row[4]):
                #     continue
                curWO["LOCATION"] = row[5]
                newaddr = re.sub(r"\s(?=SANTA)", ", ", row[4])
                newaddr = re.sub(r"ROSA\s(?=CA)", "ROSA, ", newaddr)
                newaddr = re.sub(r"(?<=CA).*", "", newaddr)
                curWO["WOADDRESS"] = newaddr
                curWO["WOXCOORDINATE"] = float(row[6]) if row[6] is not None else None
                curWO["WOYCOORDINATE"] = float(row[7]) if row[7] is not None else None
                self.workorders.append(curWO)
                self.woids.add(WORKORDERID)

            cursor.close()
        conn.close()

    def getOpenWOsByName(self, name="Troy"):
        with pyodbc.connect(self.connection_string) as conn:
            cursor = conn.cursor()
            query = (
                f"SELECT TOP 10 WORKORDERID, SUBMITTO, DESCRIPTION, STATUS, WOADDRESS, LOCATION, WOXCOORDINATE, WOYCOORDINATE FROM AZTECA.WORKORDER \
                        WHERE STATUS IN ('1OPEN', '2ASSIGNED', '3WORK IN PROGRESS') \
                            AND SUBMITTO LIKE '%{name}%'  AND WOADDRESS LIKE '%SANTA ROSA%' \
                            AND WOXCOORDINATE IS NOT NULL AND WOYCOORDINATE IS NOT NULL  \
                        ORDER BY INITIATEDATE DESC;"
            )
            print(query, file=sys.stderr)
            cursor.execute(query)
            rows = cursor.fetchall()
            for row in rows:
                curWO = {}
                WORKORDERID = row[0]
                curWO["WORKORDERID"] = WORKORDERID
                curWO["SUBMITTO"] = row[1]
                curWO["DESCRIPTION"] = row[2]
                curWO["STATUS"] = row[3]
                # curWO['ENTITIES'] = []
                curWO["WOADDRESS"] = row[4]
                # if re.search(r"^\s*0", row[4]):
                #     continue
                curWO["LOCATION"] = row[5]
                newaddr = re.sub(r"\s(?=SANTA)", ", ", row[4])
                newaddr = re.sub(r"ROSA\s(?=CA)", "ROSA, ", newaddr)
                newaddr = re.sub(r"(?<=CA).*", "", newaddr)
                curWO["WOADDRESS"] = newaddr
                curWO["WOXCOORDINATE"] = float(row[6]) if row[6] is not None else None
                curWO["WOYCOORDINATE"] = float(row[7]) if row[7] is not None else None
                self.workorders.append(curWO)
                self.woids.add(WORKORDERID)

            cursor.close()
        conn.close()

    def calculateWODistances(self, start="93 Stony Circle, Santa Rosa, CA", use_road=False):
        """Calculate pairwise distances between all workorders and the starting point.
        If use_road is True and OSMnx/networkx are available, compute road-network distances (meters -> miles) using OSMnx.
        Falls back to the previous euclidean-style cached calculateDistance() approach when OSMnx is unavailable.
        """
        print(f"Starting address: {start}\n", file=sys.stderr)
        distance_matrix = []
        geocoder = Nominatim(user_agent="myApp", timeout=10)

        geocoded = geocoder.geocode(start)
        place1 = (geocoded.latitude, geocoded.longitude)
        startWO = {
            "WORKORDERID": "start",
            "WOADDRESS": start,
            "WOXCOORDINATE": place1[1],
            "WOYCOORDINATE": place1[0],
        }
        self.workorders.append(startWO)

        # Build coordinate lookup: WORKORDERID -> (lat, lon)
        coords = {}
        for wo in self.workorders:
            coords[wo["WORKORDERID"]] = (wo["WOYCOORDINATE"], wo["WOXCOORDINATE"])  # lat, lon

        # If requested and available, use OSMnx + NetworkX for road-aware distances
        if use_road and ox is not None and nx is not None:
            # bounding box with padding
            lats = [c[0] for c in coords.values()]
            lons = [c[1] for c in coords.values()]
            north = max(lats) + 0.02
            south = min(lats) - 0.02
            east = max(lons) + 0.02
            west = min(lons) - 0.02

            # Prefer building graph by center point + radius for compatibility across osmnx versions
            center_lat = sum(lats) / len(lats)
            center_lon = sum(lons) / len(lons)

            def haversine_m(lat1, lon1, lat2, lon2):
                R = 6371000
                dlat = math.radians(lat2 - lat1)
                dlon = math.radians(lon2 - lon1)
                a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
                return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

            max_d = 0.0
            for (lat, lon) in coords.values():
                d = haversine_m(center_lat, center_lon, lat, lon)
                if d > max_d:
                    max_d = d

            # add padding (meters)
            radius = int(max_d + 2000)
            print(f"Building OSMnx graph centered at ({center_lat},{center_lon}) with radius {radius}m", file=sys.stderr)
            G = ox.graph_from_point((center_lat, center_lon), dist=radius, network_type="drive")

            # Enrich graph edges with travel_time (seconds) using maxspeed when available, else default speed
            default_mph = 25.0
            default_mps = default_mph * 0.44704
            for u, v, k, data in G.edges(keys=True, data=True):
                length = data.get("length", None)
                if length is None:
                    # skip edges without length
                    continue
                # parse maxspeed if present
                maxspeed = data.get("maxspeed")
                speed_mps = None
                if maxspeed is not None:
                    try:
                        # maxspeed can be list or string like '25 mph' or '40' or ['signals']
                        if isinstance(maxspeed, list) or isinstance(maxspeed, tuple):
                            maxspeed_val = maxspeed[0]
                        else:
                            maxspeed_val = maxspeed
                        # extract number
                        m = re.search(r"(\d+)", str(maxspeed_val))
                        if m:
                            speed_mph = float(m.group(1))
                            speed_mps = speed_mph * 0.44704
                    except Exception:
                        speed_mps = None
                if speed_mps is None:
                    speed_mps = default_mps
                # travel_time in seconds
                travel_time = float(length) / float(speed_mps) if speed_mps > 0 else float("inf")
                data["travel_time"] = travel_time

            # Map each workorder to nearest graph node
            node_map = {}
            for woid, (lat, lon) in coords.items():
                try:
                    if transformer is not None and node_positions:
                        # transform lon,lat -> projected x,y
                        x, y = transformer.transform(lon, lat)
                        # brute-force nearest node search (fast enough for small graphs)
                        best = None
                        bestd = float("inf")
                        for n, (nx_, ny_) in node_positions.items():
                            dx = nx_ - x
                            dy = ny_ - y
                            d = dx * dx + dy * dy
                            if d < bestd:
                                bestd = d
                                best = n
                        node = best
                    else:
                        # fallback to osmnx nearest_nodes on unprojected graph (may require scikit-learn)
                        node = ox.nearest_nodes(G, lon, lat)
                except Exception as e:
                    print(f"Nearest node lookup failed for {woid}: {e}", file=sys.stderr)
                    node = None
                node_map[woid] = node

            def _compute_osm_distance_and_time(G, na, nb, a, b):
                try:
                    meters = nx.shortest_path_length(G, na, nb, weight="length")
                    seconds = nx.shortest_path_length(G, na, nb, weight="travel_time")
                    miles = meters * 0.000621371
                    return (a, b, float(miles), float(seconds))
                except Exception:
                    return (a, b, float("inf"), float("inf"))

            # Compute pairwise distances in parallel
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = []
                woid_list = list(coords.keys())
                for i in range(len(woid_list)):
                    for j in range(i + 1, len(woid_list)):
                        a = woid_list[i]
                        b = woid_list[j]
                        na = node_map.get(a)
                        nb = node_map.get(b)
                        if na is None or nb is None:
                            continue
                        futures.append(executor.submit(_compute_osm_distance_and_time, G, na, nb, a, b))

                # store times in a mapping for tiebreakers
                self.pair_times = {}

                for future in as_completed(futures):
                    src, dest, miles, seconds = future.result()
                    if miles == float("inf") or seconds == float("inf"):
                        continue
                    if [src, dest, miles] in distance_matrix:
                        continue
                    distance_matrix.append([src, dest, miles])
                    distance_matrix.append([dest, src, miles])
                    self.pair_times[(src, dest)] = seconds
                    self.pair_times[(dest, src)] = seconds

            return distance_matrix

        # Fall back to original geodesic/euclidean cached calculation
        futures = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            # distances from start to others
            for j in range(1, len(self.workorders)):
                futures.append(executor.submit(calculateDistance, startWO, self.workorders[j]))
            for future in as_completed(futures):
                result = future.result()
                if result == float("inf"):
                    continue
                src, dest, dist = result
                if [src, dest, dist] in distance_matrix:
                    continue
                distance_matrix.append(["start", dest, dist])

            # pairwise distances
            futures = []
            allPairs = list(itertools.combinations(self.workorders, 2))
            for startnode, dest in allPairs:
                futures.append(executor.submit(calculateDistance, startnode, dest))
            for future in as_completed(futures):
                result = future.result()
                if result == float("inf"):
                    continue
                src, dest, dist = result
                if [src, dest, dist] in distance_matrix:
                    continue
                distance_matrix.append([src, dest, dist])
                distance_matrix.append([dest, src, dist])

            concurrent.futures.wait(futures)

        return distance_matrix

    def dijkstraShortestPath(self, graph, start="start"):
        """Correct single-source Dijkstra. Treats graph as undirected (adds reverse edges).
        Returns dict: node -> {distance, path}
        """
        adj = {}
        nodes = set()
        for source, dest, weight in graph:
            adj.setdefault(source, []).append((dest, weight))
            adj.setdefault(dest, []).append(
                (source, weight)
            )  # ensure undirected completeness
            nodes.add(source)
            nodes.add(dest)

        import math

        dist = {n: math.inf for n in nodes}
        dist[start] = 0
        parent = {start: None}
        minHeap = [(0, start)]

        while minHeap:
            d_u, u = heapq.heappop(minHeap)
            if d_u > dist[u]:
                continue
            for v, w_uv in adj.get(u, []):
                alt = d_u + w_uv
                if alt < dist.get(v, math.inf):
                    dist[v] = alt
                    parent[v] = u
                    heapq.heappush(minHeap, (alt, v))

        paths = {}
        for node in nodes:
            if dist[node] == math.inf:
                continue
            cur = node
            p = []
            while cur is not None:
                p.append(cur)
                cur = parent.get(cur)
            p.reverse()
            paths[node] = {"distance": dist[node], "path": p}
        return paths

    def tsp_nearest_neighbor_2opt(
        self, distance_graph, start="start", return_to_start=False
    ):
        """
        TSP heuristic:
        1) Nearest neighbor construction
        2) 2-opt local optimization

        distance_graph: list of (src, dst, dist)
        """

        # -----------------------------
        # 1. Build symmetric matrix
        # -----------------------------
        nodes = set()
        dist = {}

        for s, d, w in distance_graph:
            nodes.add(s)
            nodes.add(d)
            dist.setdefault(s, {})[d] = w
            dist.setdefault(d, {})[s] = w

        for u in nodes:
            dist.setdefault(u, {})
            for v in nodes:
                if u == v:
                    dist[u][v] = 0.0
                else:
                    dist[u].setdefault(v, float("inf"))

        # -----------------------------
        # 2. Nearest neighbor build
        # -----------------------------
        unvisited = set(nodes)
        unvisited.discard(start)

        route = [start]
        cur = start

        while unvisited:
            nxt = min(unvisited, key=lambda v: dist[cur][v])
            route.append(nxt)
            unvisited.remove(nxt)
            cur = nxt

        if return_to_start:
            route.append(start)

        # -----------------------------
        # 3. 2-opt improvement (delta cost version)
        # -----------------------------
        def edge(a, b):
            return dist[a][b]

        improved = True

        while improved:
            improved = False
            n = len(route)

            for i in range(1, n - 2):
                a, b = route[i - 1], route[i]

                for j in range(i + 1, n - 1):
                    c, d = route[j], route[j + 1]

                    # current edges vs swapped edges
                    if edge(a, b) + edge(c, d) > edge(a, c) + edge(b, d):
                        # perform 2-opt swap by reversing the segment between i and j (inclusive)
                        route[i : j + 1] = route[i : j + 1][::-1]
                        improved = True
                        break

                if improved:
                    break

        # -----------------------------
        # 4. Final distance (single pass)
        # -----------------------------
        total = 0.0
        for i in range(len(route) - 1):
            total += dist[route[i]][route[i + 1]]

        return {"route": route, "distance": total}

    def pathToGoogleMapsLink(self, path):
        """
        Converts a path (list of work order IDs) to a Google Maps shareable route link.

        Args:
            path (list): List of work order IDs representing the route

        Returns:
            str: Google Maps URL with waypoints
        """
        if not path or len(path) < 2:
            return None

        # Get coordinates for each node in the path
        waypoints = []
        for node_id in path:
            # Find the work order matching this ID
            wo = next(
                (wo for wo in self.workorders if wo["WORKORDERID"] == node_id), None
            )
            if wo:
                lat = wo["WOYCOORDINATE"]
                lon = wo["WOXCOORDINATE"]
                waypoints.append((lat, lon))

        if len(waypoints) < 2:
            return None

        # Build Google Maps URL using forward slashes as separators
        # Format: https://www.google.com/maps/dir/lat1,lon1/lat2,lon2/lat3,lon3/...
        waypoints_str = "/".join([f"{lat},{lon}" for lat, lon in waypoints])

        # Construct the URL
        base_url = "https://www.google.com/maps/dir/"
        google_maps_url = f"{base_url}{waypoints_str}"

        return google_maps_url

    def tsp_bruteforce(self, distance_graph, start="start", return_to_start=False, max_nodes=10):
        """Evaluate all permutations of nodes (excluding start) and return the exact best route.

        Uses the provided distance_graph (list of [src, dest, dist]). Works with any distance matrix (OSMnx or euclidean).
        For performance and safety, refuses to run when number of waypoints (excluding start) exceeds max_nodes.
        """
        # Build distance dict
        nodes = set()
        dist = {}
        for s, d, w in distance_graph:
            nodes.add(s)
            nodes.add(d)
            dist.setdefault(s, {})[d] = w
            dist.setdefault(d, {})[s] = w

        for u in nodes:
            dist.setdefault(u, {})
            for v in nodes:
                if u == v:
                    dist[u][v] = 0.0
                else:
                    dist[u].setdefault(v, float("inf"))

        waypoints = [n for n in nodes if n != start]
        n = len(waypoints)
        if n == 0:
            return {"route": [start], "distance": 0.0}
        if n > max_nodes:
            print(f"Bruteforce requested but {n} waypoints > max_nodes ({max_nodes}). Aborting bruteforce.", file=sys.stderr)
            return None

        best_route = None
        best_distance = float("inf")
        best_time = float("inf")

        for perm in itertools.permutations(waypoints):
            route = [start] + list(perm)
            if return_to_start:
                route = route + [start]
            total = 0.0
            total_time = 0.0
            feasible = True
            for i in range(len(route) - 1):
                a = route[i]
                b = route[i + 1]
                d_ab = dist.get(a, {}).get(b, float("inf"))
                if not math.isfinite(d_ab):
                    feasible = False
                    break
                total += d_ab
                # accumulate time if available
                if hasattr(self, 'pair_times'):
                    t_ab = self.pair_times.get((a, b), None)
                    if t_ab is None:
                        # missing time -> treat as infeasible
                        feasible = False
                        break
                    total_time += t_ab

            if not feasible:
                continue
            # prefer smaller distance; tie-break by time
            if (total < best_distance) or (math.isclose(total, best_distance) and total_time < best_time):
                best_distance = total
                best_route = route
                best_time = total_time

        if best_route is None:
            return None
        return {"route": best_route, "distance": best_distance, "time_seconds": best_time}

    def __str__(self):
        val = f""
        outputFile = open("workOrdersInfo.txt", "a")
        for wo in self.workorders:
            val += f"{wo['WORKORDERID']} - {wo['SUBMITTO']} - {wo['DESCRIPTION']} - {wo['STATUS']} - {wo['WOADDRESS']} - {wo['LOCATION']}\n"
            # for entity in wo['ENTITIES']:
            #    val += f"{entity['address']} - {entity['entityuid']} - {entity['entitytype']}\n"
            val += f"=" * 125 + f"\n"
        outputFile.write(val)
        outputFile.close()
        return val


import time


def memoize(func):
    cache = {}

    @wraps(func)
    def memoized_func(*args):
        wo1 = args[0]["WOADDRESS"] if type(args[0]) == dict else args[0]
        wo2 = args[1]["WOADDRESS"]
        _args = (wo1, wo2)
        if _args in cache:
            return cache[_args]
        result = func(*args)
        # tuples are hashable (since they are immutable & assuming their elements are all immutable), and they allow for 2+ elements.
        woid1, woid2, dist = result
        cache[(wo2, wo1)] = [
            woid2,
            woid1,
            dist,
        ]  # swap placement of woid1 & woid2 to maintain consistent order with cached set
        cache[_args] = result
        return result

    return memoized_func


@memoize
def calculateDistance(workOrder1, workOrder2):
    addr1 = workOrder1["WOADDRESS"] if type(workOrder1) == dict else workOrder1
    addr2 = workOrder2["WOADDRESS"]
    woid1 = workOrder1["WORKORDERID"] if type(workOrder1) == dict else "start"
    woid2 = workOrder2["WORKORDERID"]
    geocoder = Nominatim(user_agent="myApp", timeout=10)
    place1 = None
    if type(workOrder1) != dict:
        geocoded = geocoder.geocode(addr1)
        place1 = (geocoded.latitude, geocoded.longitude)
    else:
        place1 = (workOrder1["WOYCOORDINATE"], workOrder1["WOXCOORDINATE"])
    place2 = (workOrder2["WOYCOORDINATE"], workOrder2["WOXCOORDINATE"])

    distance = math.sqrt(
        (float(place2[0]) - float(place1[0])) ** 2
        + (float(place2[1]) - float(place1[1])) ** 2
    )
    print(f"calculateDistance called on : {addr1} and {addr2}", file=sys.stderr)
    try:
        # return [woid1, woid2, float(f"{distance.geodesic(place1, place2).miles:.3f}")]
        return [woid1, woid2, float(distance)]

        # lat1, lon1 = place1
        # lat2, lon2 = place2
        # return [woid1, woid2, float(f"{euclidean_distance_miles(lat1, lon1, lat2, lon2):.3f}")]
        # return [addr1, addr2, float(f"{distance.geodesic(place1, place2).miles:.3f}")]
    except Exception as e:
        print(f"{addr1} vs {addr2} - Exception: {str(e)}", file=sys.stderr)
        return float("inf")


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(
        description="find closest Work Orders from starting point"
    )
    parser.add_argument(
        "-sa", "--starting_address", type=str, help="Address of starting location"
    )
    parser.add_argument(
        "-s", "--submitto_name", type=str, help="Name of person to filter WOs by"
    )
    parser.add_argument(
        "-w", "--woids", type=str, help="Comma-separated list of Work Order IDs"
    )
    parser.add_argument("--use_osm", action="store_true", help="Use OSMnx road-aware distances (requires osmnx)")
    parser.add_argument("--bruteforce", action="store_true", help="Evaluate all permutations (exact) - practical for <=10 waypoints")
    # parser.add_argument('-h', '--help', action='help', default=argparse.SUPPRESS, help='-s (--submitto_name) -sa (--starting_address)')
    args = parser.parse_args()
    submitto = args.submitto_name
    starting_address = args.starting_address
    woids_arg = args.woids
    use_osm = args.use_osm
    bruteforce = args.bruteforce
    _sql = SQL_Executor()
    if woids_arg:
        woids = [w.strip() for w in woids_arg.split(',') if w.strip()]
        _sql.getOpenWOsById(woids)
    elif submitto:
        _sql.getOpenWOsByName(submitto)
    else:
        # default: try to get by empty submitto (behaviour preserved)
        _sql.getOpenWOsByName(submitto)
    print(str(_sql), file=sys.stderr)

    start_time = time.perf_counter()

    distanceGraph = None
    if starting_address:
        distanceGraph = _sql.calculateWODistances(starting_address, use_road=use_osm)
    else:
        distanceGraph = _sql.calculateWODistances(use_road=use_osm)

    end_time = time.perf_counter()
    print(f"calculateWODistance exec time: {end_time - start_time:.4f} seconds", file=sys.stderr)

    print(distanceGraph, file=sys.stderr)

    # If bruteforce requested, attempt exact permutation evaluation (subject to max size)
    tsp_result = None
    if bruteforce:
        bruteforce_result = _sql.tsp_bruteforce(distanceGraph, start="start", return_to_start=False, max_nodes=10)
        if bruteforce_result is None:
            print("Bruteforce not performed (too many waypoints or infeasible). Falling back to heuristic.", file=sys.stderr)
            tsp_result = _sql.tsp_nearest_neighbor_2opt(distanceGraph, start="start", return_to_start=False)
        else:
            tsp_result = bruteforce_result
    else:
        # Use heuristic TSP (nearest neighbor + 2-opt) to get a route visiting all nodes
        tsp_result = _sql.tsp_nearest_neighbor_2opt(
            distanceGraph, start="start", return_to_start=False
        )

    route = tsp_result.get("route", [])
    total_dist = tsp_result.get("distance", float("inf"))
    print(f"TSP route: {route}", file=sys.stderr)
    print(f"TSP total distance: {total_dist}", file=sys.stderr)
    print(list(_sql.woids), file=sys.stderr)
    # Remove duplicates while preserving order (shouldn't be any duplicates in route)
    seen = []
    for item in route:
        if item not in seen:
            seen.append(item)
    print(seen, file=sys.stderr)

    # Generate Google Maps link for the full route (will include 'start' node if present)
    googleMapsLink = _sql.pathToGoogleMapsLink(list(seen))
    print(f"Google Maps Link for TSP route: {googleMapsLink}", file=sys.stderr)

    # Prepare JSON result for API consumers
    result = {
        "route": route,
        "distance": total_dist,
        "googleMapsLink": googleMapsLink,
        "woids": list(_sql.woids),
        "workorders": _sql.workorders,
    }
    # Ensure JSON-safe values: convert non-finite floats (Infinity/-Infinity/NaN) to None
    if not (isinstance(result.get("distance"), float) and math.isfinite(result["distance"])):
        result["distance"] = None

    def sanitize(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, float) and not math.isfinite(v):
                    obj[k] = None
                else:
                    sanitize(v)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, float) and not math.isfinite(item):
                    obj[i] = None
                else:
                    sanitize(item)

    sanitize(result)
    print(json.dumps(result))

    outFile = open("workOrdersInfo.txt", "a")
    outFile.write(str({"route": route, "distance": total_dist}))
    outFile.write("\n")
    distanceGraph = sorted(distanceGraph, key=lambda x: x[0])
    for arr in distanceGraph:
        for val in arr:
            outFile.write(f"{val: >10}")
        outFile.write("\tMiles\n")

    outFile.write("\nSuggested Groupings:\n")
    # suggested Grouping
    distancesLTOne = list(filter(lambda x: x[2] < 2, distanceGraph))
    distancesLTOne = sorted(distancesLTOne, key=lambda x: x[2])
    visited = set()
    for src, dest, dist in distancesLTOne:
        if (src, dest) in visited:
            continue
        visited.add((src, dest))
        visited.add((dest, src))
        woaddress1 = list(filter(lambda x: x["WORKORDERID"] == src, _sql.workorders))
        if len(woaddress1) > 0:
            woaddress1 = woaddress1[0]["WOADDRESS"]
        else:
            woaddress1 = dist[0]
        woaddress2 = list(filter(lambda x: x["WORKORDERID"] == dest, _sql.workorders))[
            0
        ]["WOADDRESS"]
        outFile.write(f"{woaddress1:>40} {woaddress2:>40} {dist:>10} Miles\n")
        outFile.write(f"{src:>40} {dest:>40} \n\n")

    outFile.close()
