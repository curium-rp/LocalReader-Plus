/**
 * Native C++ ZIP stream engine (zipstream)
 * ----------------------------------------
 * Read-only ZIP streamer: seeks the EPUB container in place. Never writes
 * extracted files to disk.
 *
 * Compilation & Tools Reference:
 *   Windows (MSVC x64 - Recommended):
 *     cl.exe /O2 /LD /utf-8 /EHsc /std:c++17 -DZIPSTREAM_DLL /I zlib_inc zipstream.cpp stack_chk.c /Fe:..\zipstream-win-x64.dll C:\msys64\ucrt64\lib\libz.a /link /OPT:REF /OPT:ICF
 *     -> Result: 371 KB (dynamic UCRT, 72% smaller)
 *   Windows (MinGW GCC Fallback):
 *     g++ -shared -O3 -std=c++17 -DZIPSTREAM_DLL zipstream.cpp -o ..\zipstream-win-x64.dll -lz -static -s
 *     -> Result: 1.33 MB (static libstdc++)
 *   macOS (Zig cross-compile: ARM64 & x86_64):
 *     zig c++ -target aarch64-macos -O3 -w -std=c++17 -DZIPSTREAM_DLL -shared zipstream.cpp -o ..\zipstream-darwin-arm64.dylib -lc++ -L. -lz
 *     -> Result: 708 KB (ARM64 Apple Silicon)
 *     zig c++ -target x86_64-macos -O3 -w -std=c++17 -DZIPSTREAM_DLL -shared zipstream.cpp -o ..\zipstream-darwin-x64.dylib -lc++ -L. -lz
 *     -> Result: 634 KB (x86_64 Intel Mac)
 *   Linux (GCC via WSL / native):
 *     g++ -shared -fPIC -O3 -std=c++17 -DZIPSTREAM_DLL -static-libgcc -static-libstdc++ -s zipstream.cpp -o ../zipstream-linux-x64.so -lz
 *     -> Result: 1.73 MB (static libstdc++, cross-distro safe)
 *
 * CLI (non-DLL builds):
 *   zipstream manifest <epub_path>
 *   zipstream single <epub_path> <inner_path>
 *   zipstream stream_html <epub_path>
 *   zipstream stats <html|css|image|all> <epub_path>
 */

#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <chrono>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <limits>
#include <algorithm>
#include <unordered_map>
#include <zlib.h>

#include <filesystem>

#if defined(_WIN32) || defined(_WIN64)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#define SET_BINARY_MODE(handle) _setmode(_fileno(handle), _O_BINARY)

#ifndef ZIPSTREAM_DLL
static std::vector<std::wstring> win_wide_args() {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::vector<std::wstring> out;
    if (argv) {
        for (int i = 0; i < argc; ++i) out.emplace_back(argv[i]);
        LocalFree(argv);
    }
    return out;
}

static std::string wide_to_utf8(const std::wstring& wide) {
    if (wide.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, nullptr, 0, nullptr, nullptr);
    if (n <= 1) return {};
    std::string out(static_cast<size_t>(n - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, &out[0], n, nullptr, nullptr);
    return out;
}
#endif

static std::filesystem::path make_win_long_path(const std::filesystem::path& input) {
    std::filesystem::path abs = input;
    std::error_code ec;
    auto canon = std::filesystem::absolute(input, ec);
    if (!ec) abs = canon;
    std::wstring s = abs.wstring();
    if (s.compare(0, 4, L"\\\\?\\") == 0) return abs;
    if (s.compare(0, 2, L"\\\\") == 0) {
        return std::filesystem::path(L"\\\\?\\UNC\\" + s.substr(2));
    }
    return std::filesystem::path(L"\\\\?\\" + s);
}
#else
#define SET_BINARY_MODE(handle) ((void)0)
#endif

static std::filesystem::path path_from_utf8(const char* utf8) {
    if (!utf8 || !*utf8) return {};
#if defined(_WIN32) || defined(_WIN64)
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, nullptr, 0);
    if (n <= 1) return {};
    std::wstring wide(static_cast<size_t>(n - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8, -1, wide.data(), n);
    return std::filesystem::path(wide);
#else
    return std::filesystem::u8path(utf8);
#endif
}

#if defined(_WIN32) || defined(_WIN64)
#define ZIPSTREAM_API extern "C" __declspec(dllexport)
#else
#define ZIPSTREAM_API extern "C"
#endif

static constexpr uint64_t kMaxZipMembers = 100000;
static constexpr uint64_t kMaxSingleBytes = 512ull * 1024ull * 1024ull;
static constexpr uint64_t kMaxHtmlEntryBytes = 32ull * 1024ull * 1024ull;
static constexpr uint64_t kMaxHtmlStreamBytes = 256ull * 1024ull * 1024ull;
static constexpr uint64_t kMaxXmlBytes = 8ull * 1024ull * 1024ull;
static constexpr uint16_t kZipEncrypted = 0x0001;
static constexpr uint32_t kSigLocal = 0x04034b50u;
static constexpr uint32_t kSigCentral = 0x02014b50u;
static constexpr uint32_t kSigEocd = 0x06054b50u;
static constexpr uint32_t kSigZip64Eocd = 0x06064b50u;
static constexpr uint32_t kSigZip64Locator = 0x07064b50u;

#pragma pack(push, 1)
struct ZipEOCD {
    uint32_t signature;
    uint16_t disk_number;
    uint16_t start_disk;
    uint16_t records_on_disk;
    uint16_t total_records;
    uint32_t cd_size;
    uint32_t cd_offset;
    uint16_t comment_len;
};

struct ZipCDFileHeader {
    uint32_t signature;
    uint16_t version_made_by;
    uint16_t version_needed;
    uint16_t flags;
    uint16_t method;
    uint16_t last_mod_time;
    uint16_t last_mod_date;
    uint32_t crc32;
    uint32_t comp_size;
    uint32_t uncomp_size;
    uint16_t name_len;
    uint16_t extra_len;
    uint16_t comment_len;
    uint16_t disk_num;
    uint16_t internal_attrs;
    uint32_t external_attrs;
    uint32_t local_header_offset;
};

struct ZipLocalHeader {
    uint32_t signature;
    uint16_t version_needed;
    uint16_t flags;
    uint16_t method;
    uint16_t last_mod_time;
    uint16_t last_mod_date;
    uint32_t crc32;
    uint32_t comp_size;
    uint32_t uncomp_size;
    uint16_t name_len;
    uint16_t extra_len;
};

struct Zip64Locator {
    uint32_t signature;
    uint32_t disk_number;
    uint64_t zip64_eocd_offset;
    uint32_t total_disks;
};
#pragma pack(pop)

struct EntryMeta {
    std::string name;
    uint16_t method = 0;
    uint16_t flags = 0;
    uint64_t comp_size = 0;
    uint64_t uncomp_size = 0;
    uint64_t local_offset = 0;
    uint32_t crc32 = 0;
};

struct PeekStats {
    std::string mode;
    int count = 0;
    size_t total_bytes = 0;
    uint32_t xor_crc = 0;
    double elapsed_ms = 0.0;
};

struct OpfPackage {
    std::string title;
    std::string language;
    std::string opf_path;
    std::string nav_href;
    std::string ncx_href;
    std::vector<std::string> spine;
};

static char ascii_tolower(char ch) {
    auto c = static_cast<unsigned char>(ch);
    if (c >= 'A' && c <= 'Z') return static_cast<char>(c - 'A' + 'a');
    return static_cast<char>(c);
}

static bool ascii_ieq(const std::string& a, const std::string& b) {
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); ++i) {
        if (ascii_tolower(a[i]) != ascii_tolower(b[i])) return false;
    }
    return true;
}

static bool ascii_ieq_cstr(const char* a, size_t n, const char* b) {
    const size_t m = std::strlen(b);
    if (n != m) return false;
    for (size_t i = 0; i < n; ++i) {
        if (ascii_tolower(a[i]) != ascii_tolower(b[i])) return false;
    }
    return true;
}

static bool has_ascii_suffix(const std::string& name, const char* suffix) {
    const size_t n = name.size();
    const size_t m = std::strlen(suffix);
    if (n < m) return false;
    for (size_t i = 0; i < m; ++i) {
        if (ascii_tolower(name[n - m + i]) != ascii_tolower(suffix[i])) return false;
    }
    return true;
}

static bool is_html_name(const std::string& name) {
    return has_ascii_suffix(name, ".xhtml") ||
           has_ascii_suffix(name, ".html") ||
           has_ascii_suffix(name, ".htm");
}

static bool is_css_name(const std::string& name) {
    return has_ascii_suffix(name, ".css");
}

static bool is_image_name(const std::string& name) {
    return has_ascii_suffix(name, ".jpg") || has_ascii_suffix(name, ".jpeg") ||
           has_ascii_suffix(name, ".png") || has_ascii_suffix(name, ".webp") ||
           has_ascii_suffix(name, ".gif") || has_ascii_suffix(name, ".svg");
}

static bool is_dir_name(const std::string& name) {
    return name.empty() || name.back() == '/';
}

static bool is_ident_char(char ch) {
    auto c = static_cast<unsigned char>(ch);
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') || c == '_' || c == '-' || c == ':';
}

static uint16_t read_u16_le(const uint8_t* p) {
    return static_cast<uint16_t>(p[0] | (static_cast<uint16_t>(p[1]) << 8));
}

static uint32_t read_u32_le(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) |
           (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}

static uint64_t read_u64_le(const uint8_t* p) {
    return static_cast<uint64_t>(read_u32_le(p)) |
           (static_cast<uint64_t>(read_u32_le(p + 4)) << 32);
}

static int utf8_seq_len(unsigned char c) {
    if ((c & 0x80) == 0) return 1;
    if ((c & 0xE0) == 0xC0) return 2;
    if ((c & 0xF0) == 0xE0) return 3;
    if ((c & 0xF8) == 0xF0) return 4;
    return 0;
}

static bool utf8_seq_ok(const std::string& s, size_t i, int len) {
    if (len <= 1 || i + static_cast<size_t>(len) > s.size()) return false;
    for (int k = 1; k < len; ++k) {
        if ((static_cast<unsigned char>(s[i + static_cast<size_t>(k)]) & 0xC0) != 0x80) return false;
    }
    return true;
}

static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    size_t i = 0;
    while (i < s.size()) {
        const auto c = static_cast<unsigned char>(s[i]);
        switch (c) {
            case '"':  out += "\\\""; ++i; continue;
            case '\\': out += "\\\\"; ++i; continue;
            case '\b': out += "\\b"; ++i; continue;
            case '\f': out += "\\f"; ++i; continue;
            case '\n': out += "\\n"; ++i; continue;
            case '\r': out += "\\r"; ++i; continue;
            case '\t': out += "\\t"; ++i; continue;
            default: break;
        }
        if (c < 0x20) {
            char buf[8];
            std::snprintf(buf, sizeof(buf), "\\u%04x", c);
            out += buf;
            ++i;
            continue;
        }
        const int len = utf8_seq_len(c);
        if (len == 1) {
            out += static_cast<char>(c);
            ++i;
            continue;
        }
        if (utf8_seq_ok(s, i, len)) {
            out.append(s, i, static_cast<size_t>(len));
            i += static_cast<size_t>(len);
        } else {
            out += "\\ufffd";
            ++i;
        }
    }
    return out;
}

static std::string json_str(const std::string& s) {
    return "\"" + json_escape(s) + "\"";
}

static std::string json_str_array(const std::vector<std::string>& items) {
    std::ostringstream oss;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i) oss << ", ";
        oss << json_str(items[i]);
    }
    return oss.str();
}

static std::string xml_unescape(std::string s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] != '&') {
            out += s[i];
            continue;
        }
        if (s.compare(i, 5, "&amp;") == 0) { out += '&'; i += 4; continue; }
        if (s.compare(i, 4, "&lt;") == 0) { out += '<'; i += 3; continue; }
        if (s.compare(i, 4, "&gt;") == 0) { out += '>'; i += 3; continue; }
        if (s.compare(i, 6, "&quot;") == 0) { out += '"'; i += 5; continue; }
        if (s.compare(i, 6, "&apos;") == 0) { out += '\''; i += 5; continue; }
        out += '&';
    }
    return out;
}

static int hex_nibble(char ch) {
    auto c = static_cast<unsigned char>(ch);
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static std::string url_decode(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size()) {
            const int hi = hex_nibble(s[i + 1]);
            const int lo = hex_nibble(s[i + 2]);
            if (hi >= 0 && lo >= 0) {
                out += static_cast<char>((hi << 4) | lo);
                i += 2;
                continue;
            }
        }
        out += s[i];
    }
    return out;
}

static std::string strip_query_fragment(std::string s) {
    const size_t hash = s.find('#');
    if (hash != std::string::npos) s.resize(hash);
    const size_t q = s.find('?');
    if (q != std::string::npos) s.resize(q);
    return s;
}

static std::string posix_dir(const std::string& path) {
    const size_t slash = path.rfind('/');
    if (slash == std::string::npos) return {};
    return path.substr(0, slash);
}

static std::string posix_norm(const std::string& path) {
    std::vector<std::string> parts;
    std::string cur;
    for (size_t i = 0; i <= path.size(); ++i) {
        if (i == path.size() || path[i] == '/') {
            if (cur.empty() || cur == ".") {
                cur.clear();
                continue;
            }
            if (cur == "..") {
                if (!parts.empty()) parts.pop_back();
                cur.clear();
                continue;
            }
            parts.push_back(cur);
            cur.clear();
        } else {
            cur += path[i];
        }
    }
    std::string out;
    for (size_t i = 0; i < parts.size(); ++i) {
        if (i) out += '/';
        out += parts[i];
    }
    return out;
}

static std::string posix_join(const std::string& dir, const std::string& rel) {
    std::string r = rel;
    while (!r.empty() && r.front() == '/') r.erase(r.begin());
    if (dir.empty()) return posix_norm(r);
    return posix_norm(dir + "/" + r);
}

static std::string trim_ws(const std::string& s) {
    size_t a = 0;
    while (a < s.size() && static_cast<unsigned char>(s[a]) <= ' ') ++a;
    size_t b = s.size();
    while (b > a && static_cast<unsigned char>(s[b - 1]) <= ' ') --b;
    return s.substr(a, b - a);
}

static size_t find_ci(const std::string& hay, const char* needle, size_t from = 0) {
    const size_t n = hay.size();
    const size_t m = std::strlen(needle);
    if (m == 0 || from >= n) return std::string::npos;
    for (size_t i = from; i + m <= n; ++i) {
        bool ok = true;
        for (size_t j = 0; j < m; ++j) {
            if (ascii_tolower(hay[i + j]) != ascii_tolower(needle[j])) {
                ok = false;
                break;
            }
        }
        if (ok) return i;
    }
    return std::string::npos;
}

static bool space_token_has(const std::string& hay, const char* tok) {
    const size_t m = std::strlen(tok);
    size_t i = 0;
    while (i < hay.size()) {
        while (i < hay.size() && (hay[i] == ' ' || hay[i] == '\t' || hay[i] == '\r' || hay[i] == '\n')) ++i;
        const size_t j = i;
        while (i < hay.size() && hay[i] != ' ' && hay[i] != '\t' && hay[i] != '\r' && hay[i] != '\n') ++i;
        if (ascii_ieq_cstr(hay.c_str() + j, i - j, tok) && m == (i - j)) return true;
    }
    return false;
}

static bool is_start_tag_local(const std::string& xml, size_t lt, const char* local) {
    if (lt >= xml.size() || xml[lt] != '<') return false;
    size_t i = lt + 1;
    if (i >= xml.size() || xml[i] == '/' || xml[i] == '!' || xml[i] == '?') return false;
    const size_t start = i;
    while (i < xml.size() && xml[i] != '>' && xml[i] != '/' &&
           xml[i] != ' ' && xml[i] != '\t' && xml[i] != '\n' && xml[i] != '\r') {
        ++i;
    }
    std::string qname = xml.substr(start, i - start);
    const size_t colon = qname.rfind(':');
    const std::string localn = (colon == std::string::npos) ? qname : qname.substr(colon + 1);
    return ascii_ieq_cstr(localn.c_str(), localn.size(), local);
}

static std::string xml_attr(const std::string& xml, size_t lt, const char* key) {
    const size_t gt = xml.find('>', lt);
    if (gt == std::string::npos || gt <= lt) return {};
    const std::string tag = xml.substr(lt, gt - lt);
    const size_t klen = std::strlen(key);
    size_t pos = 0;
    while (pos < tag.size()) {
        const size_t hit = find_ci(tag, key, pos);
        if (hit == std::string::npos) return {};
        const bool left_ok = (hit == 0) || !is_ident_char(tag[hit - 1]);
        const size_t after = hit + klen;
        const bool right_ok = (after >= tag.size()) || !is_ident_char(tag[after]);
        if (!left_ok || !right_ok) {
            pos = hit + 1;
            continue;
        }
        size_t i = after;
        while (i < tag.size() && (tag[i] == ' ' || tag[i] == '\t' || tag[i] == '\n' || tag[i] == '\r')) ++i;
        if (i >= tag.size() || tag[i] != '=') {
            pos = hit + 1;
            continue;
        }
        ++i;
        while (i < tag.size() && (tag[i] == ' ' || tag[i] == '\t' || tag[i] == '\n' || tag[i] == '\r')) ++i;
        if (i >= tag.size()) return {};
        const char quote = tag[i];
        if (quote != '"' && quote != '\'') return {};
        ++i;
        const size_t start = i;
        while (i < tag.size() && tag[i] != quote) ++i;
        return xml_unescape(tag.substr(start, i - start));
    }
    return {};
}

static std::string xml_first_text(const std::string& xml, const char* local) {
    for (size_t i = 0; i < xml.size(); ++i) {
        if (xml[i] != '<' || !is_start_tag_local(xml, i, local)) continue;
        const size_t gt = xml.find('>', i);
        if (gt == std::string::npos) return {};
        if (gt > i && xml[gt - 1] == '/') continue;
        size_t end = gt + 1;
        while (end < xml.size()) {
            if (xml[end] == '<' && end + 1 < xml.size() && xml[end + 1] == '/') break;
            ++end;
        }
        if (end >= xml.size()) return {};
        return xml_unescape(trim_ws(xml.substr(gt + 1, end - (gt + 1))));
    }
    return {};
}

static bool html_media_type(const std::string& mt) {
    std::string lower;
    lower.reserve(mt.size());
    for (char c : mt) lower += ascii_tolower(c);
    return lower.find("html") != std::string::npos;
}

static bool ncx_media_type(const std::string& mt) {
    std::string lower;
    lower.reserve(mt.size());
    for (char c : mt) lower += ascii_tolower(c);
    return lower.find("ncx") != std::string::npos;
}

static bool is_toc_or_nav_file(const std::string& href) {
    return has_ascii_suffix(href, "nav.xhtml") ||
           has_ascii_suffix(href, "toc.xhtml") ||
           has_ascii_suffix(href, "toc.ncx");
}

static void apply_zip64_extra(
    const uint8_t* extra,
    uint16_t extra_len,
    bool need_uncomp,
    bool need_comp,
    bool need_offset,
    EntryMeta& entry
) {
    size_t pos = 0;
    while (pos + 4 <= extra_len) {
        const uint16_t tag = read_u16_le(extra + pos);
        const uint16_t sz = read_u16_le(extra + pos + 2);
        pos += 4;
        if (pos + sz > extra_len) break;
        if (tag == 0x0001) {
            size_t p = 0;
            if (need_uncomp && p + 8 <= sz) {
                entry.uncomp_size = read_u64_le(extra + pos + p);
                p += 8;
            }
            if (need_comp && p + 8 <= sz) {
                entry.comp_size = read_u64_le(extra + pos + p);
                p += 8;
            }
            if (need_offset && p + 8 <= sz) {
                entry.local_offset = read_u64_le(extra + pos + p);
            }
        }
        pos += sz;
    }
}

static uint32_t crc32_bytes(const uint8_t* data, size_t n) {
    uLong crc = crc32(0L, Z_NULL, 0);
    size_t off = 0;
    while (off < n) {
        const uInt chunk = static_cast<uInt>(std::min<size_t>(n - off, 1024ull * 1024ull));
        crc = crc32(crc, data + off, chunk);
        off += chunk;
    }
    return static_cast<uint32_t>(crc);
}

static bool parse_opf_package(const std::string& xml, const std::string& opf_path, OpfPackage& out) {
    const std::string opf_dir = posix_dir(opf_path);
    std::unordered_map<std::string, std::string> id_to_href;
    std::unordered_map<std::string, std::string> id_to_media;
    std::unordered_map<std::string, std::string> id_to_props;

    out.title = xml_first_text(xml, "title");
    out.language = xml_first_text(xml, "language");

    for (size_t i = 0; i < xml.size(); ++i) {
        if (xml[i] != '<' || !is_start_tag_local(xml, i, "item")) continue;
        const std::string id = xml_attr(xml, i, "id");
        std::string href = strip_query_fragment(url_decode(xml_attr(xml, i, "href")));
        const std::string media = xml_attr(xml, i, "media-type");
        const std::string props = xml_attr(xml, i, "properties");
        if (id.empty() || href.empty()) continue;
        href = posix_join(opf_dir, href);
        id_to_href[id] = href;
        id_to_media[id] = media;
        id_to_props[id] = props;
        if (space_token_has(props, "nav")) out.nav_href = href;
        if (ncx_media_type(media) || has_ascii_suffix(href, "toc.ncx")) out.ncx_href = href;
    }

    for (size_t i = 0; i < xml.size(); ++i) {
        if (xml[i] != '<' || !is_start_tag_local(xml, i, "itemref")) continue;
        const std::string idref = xml_attr(xml, i, "idref");
        const std::string linear = xml_attr(xml, i, "linear");
        if (idref.empty()) continue;
        auto it = id_to_href.find(idref);
        if (it == id_to_href.end()) continue;
        const std::string& href = it->second;
        const std::string media = id_to_media[idref];
        const std::string props = id_to_props[idref];
        const bool navish = space_token_has(props, "nav") || is_toc_or_nav_file(href);
        // Keep end-of-book notes/references even when marked linear="no".
        // Drop only nav/toc documents so they are not extra "chapters".
        if (!linear.empty() && ascii_ieq(linear, "no") && navish) continue;
        if (!is_html_name(href) && !html_media_type(media)) continue;
        out.spine.push_back(href);
    }
    return !out.spine.empty() || !out.title.empty() || !out.nav_href.empty() || !out.ncx_href.empty();
}

class ZipStream {
private:
    std::filesystem::path path_;
    std::ifstream file_;
    std::vector<EntryMeta> entries_;
    uint64_t file_size_ = 0;

    bool seek_abs(uint64_t pos) {
        file_.clear();
        if (pos > file_size_) return false;
        file_.seekg(static_cast<std::streamoff>(pos), std::ios::beg);
        return file_.good();
    }

    bool read_exact(void* dst, size_t n) {
        if (n == 0) return true;
        if (!dst) return false;
        if (n > static_cast<size_t>(std::numeric_limits<std::streamsize>::max())) return false;
        file_.clear();
        file_.read(reinterpret_cast<char*>(dst), static_cast<std::streamsize>(n));
        return file_.good() && static_cast<size_t>(file_.gcount()) == n;
    }

    bool skip_bytes(uint64_t n) {
        if (n == 0) return true;
        const std::streamoff here = file_.tellg();
        if (here < 0) return false;
        return seek_abs(static_cast<uint64_t>(here) + n);
    }

    bool parse_central_directory() {
        file_.clear();
        file_.seekg(0, std::ios::end);
        const std::streamoff end = file_.tellg();
        if (end < 22) return false;
        file_size_ = static_cast<uint64_t>(end);

        const uint64_t search_len = (file_size_ > 65557ull) ? 65557ull : file_size_;
        const uint64_t search_start = file_size_ - search_len;
        std::vector<uint8_t> buf(static_cast<size_t>(search_len));
        if (!seek_abs(search_start) || !read_exact(buf.data(), buf.size())) return false;

        int64_t eocd_rel = -1;
        if (search_len >= 22) {
            for (int64_t i = static_cast<int64_t>(search_len) - 22; i >= 0; --i) {
                const size_t idx = static_cast<size_t>(i);
                if (buf[idx] != 0x50 || buf[idx + 1] != 0x4b || buf[idx + 2] != 0x05 || buf[idx + 3] != 0x06) {
                    continue;
                }
                const uint16_t comment_len = read_u16_le(&buf[idx + 20]);
                if (search_start + static_cast<uint64_t>(i) + 22ull + comment_len == file_size_) {
                    eocd_rel = i;
                    break;
                }
            }
        }
        if (eocd_rel < 0) return false;

        const uint64_t eocd_pos = search_start + static_cast<uint64_t>(eocd_rel);
        ZipEOCD eocd{};
        if (!seek_abs(eocd_pos) || !read_exact(&eocd, sizeof(eocd))) return false;
        if (eocd.signature != kSigEocd) return false;

        uint64_t cd_offset = eocd.cd_offset;
        uint64_t total_records = eocd.total_records;

        const bool zip64 = (eocd.total_records == 0xFFFF ||
                            eocd.cd_offset == 0xFFFFFFFFu ||
                            eocd.cd_size == 0xFFFFFFFFu);
        if (zip64) {
            if (eocd_pos < sizeof(Zip64Locator)) return false;
            Zip64Locator loc{};
            if (!seek_abs(eocd_pos - sizeof(Zip64Locator)) || !read_exact(&loc, sizeof(loc))) return false;
            if (loc.signature != kSigZip64Locator) return false;
            if (loc.zip64_eocd_offset + 56 > file_size_) return false;
            uint8_t z64[56];
            if (!seek_abs(loc.zip64_eocd_offset) || !read_exact(z64, sizeof(z64))) return false;
            if (read_u32_le(z64) != kSigZip64Eocd) return false;
            total_records = read_u64_le(z64 + 32);
            cd_offset = read_u64_le(z64 + 48);
        }

        if (total_records == 0 || total_records > kMaxZipMembers) return false;
        if (cd_offset >= file_size_) return false;
        if (!seek_abs(cd_offset)) return false;

        entries_.clear();
        entries_.reserve(static_cast<size_t>(std::min<uint64_t>(total_records, 4096)));

        for (uint64_t i = 0; i < total_records; ++i) {
            ZipCDFileHeader cdfh{};
            if (!read_exact(&cdfh, sizeof(cdfh))) break;
            if (cdfh.signature != kSigCentral) break;

            EntryMeta entry;
            entry.method = cdfh.method;
            entry.flags = cdfh.flags;
            entry.crc32 = cdfh.crc32;
            entry.comp_size = cdfh.comp_size;
            entry.uncomp_size = cdfh.uncomp_size;
            entry.local_offset = cdfh.local_header_offset;

            entry.name.assign(cdfh.name_len, '\0');
            if (cdfh.name_len && !read_exact(&entry.name[0], cdfh.name_len)) break;

            std::vector<uint8_t> extra(cdfh.extra_len);
            if (cdfh.extra_len && !read_exact(extra.data(), cdfh.extra_len)) break;
            if (cdfh.comment_len && !skip_bytes(cdfh.comment_len)) break;

            apply_zip64_extra(
                extra.data(),
                cdfh.extra_len,
                cdfh.uncomp_size == 0xFFFFFFFFu,
                cdfh.comp_size == 0xFFFFFFFFu,
                cdfh.local_header_offset == 0xFFFFFFFFu,
                entry
            );

            if (is_dir_name(entry.name)) continue;
            entries_.push_back(std::move(entry));
        }

        return !entries_.empty();
    }

    bool inflate_raw(const std::vector<uint8_t>& comp, std::vector<uint8_t>& out) const {
        if (out.empty()) return comp.empty();
        z_stream strm{};
        strm.next_in = const_cast<Bytef*>(comp.data());
        strm.avail_in = static_cast<uInt>(comp.size());
        strm.next_out = out.data();
        strm.avail_out = static_cast<uInt>(out.size());
        if (inflateInit2(&strm, -MAX_WBITS) != Z_OK) return false;
        const int ret = inflate(&strm, Z_FINISH);
        const uLong produced = strm.total_out;
        inflateEnd(&strm);
        if (ret != Z_STREAM_END || produced == 0) return false;
        if (produced < out.size()) out.resize(static_cast<size_t>(produced));
        return true;
    }

    bool load_text_entry(const std::string& inner_path, std::string& out, uint64_t max_bytes) {
        std::vector<uint8_t> buf;
        if (!decompress_entry_by_name(inner_path, buf, max_bytes)) return false;
        out.assign(reinterpret_cast<const char*>(buf.data()), buf.size());
        return true;
    }

    std::string pick_toc_file(const OpfPackage& opf) const {
        if (!opf.nav_href.empty() && find_entry(opf.nav_href)) return opf.nav_href;
        if (!opf.ncx_href.empty() && find_entry(opf.ncx_href)) return opf.ncx_href;

        std::string toc_xhtml;
        std::string nav_xhtml;
        std::string toc_ncx;
        for (const auto& e : entries_) {
            if (has_ascii_suffix(e.name, "toc.xhtml")) toc_xhtml = e.name;
            else if (has_ascii_suffix(e.name, "nav.xhtml")) nav_xhtml = e.name;
            else if (has_ascii_suffix(e.name, "toc.ncx")) toc_ncx = e.name;
        }
        if (!nav_xhtml.empty()) return nav_xhtml;
        if (!toc_xhtml.empty()) return toc_xhtml;
        return toc_ncx;
    }

    OpfPackage load_opf_package() {
        OpfPackage opf;
        std::string container_path;
        const EntryMeta* container = find_entry("META-INF/container.xml");
        if (!container) {
            for (const auto& e : entries_) {
                if (has_ascii_suffix(e.name, "container.xml") && find_ci(e.name, "meta-inf") != std::string::npos) {
                    container_path = e.name;
                    break;
                }
            }
        } else {
            container_path = container->name;
        }

        std::string opf_path;
        if (!container_path.empty()) {
            std::string container_xml;
            if (load_text_entry(container_path, container_xml, kMaxXmlBytes)) {
                const size_t root = find_ci(container_xml, "<rootfile");
                if (root != std::string::npos) {
                    opf_path = posix_norm(url_decode(xml_attr(container_xml, root, "full-path")));
                }
            }
        }

        if (opf_path.empty() || !find_entry(opf_path)) {
            for (const auto& e : entries_) {
                if (has_ascii_suffix(e.name, ".opf")) {
                    opf_path = e.name;
                    break;
                }
            }
        }
        if (opf_path.empty()) return opf;

        std::string opf_xml;
        if (!load_text_entry(opf_path, opf_xml, kMaxXmlBytes)) return opf;
        opf.opf_path = opf_path;
        parse_opf_package(opf_xml, opf_path, opf);
        return opf;
    }

public:
    static bool is_html(const std::string& name) { return is_html_name(name); }
    static bool is_css(const std::string& name) { return is_css_name(name); }
    static bool is_image(const std::string& name) { return is_image_name(name); }

    bool decompress_entry(const EntryMeta& entry, std::vector<uint8_t>& out, uint64_t max_bytes) {
        out.clear();
        if (entry.flags & kZipEncrypted) return false;
        if (entry.method != 0 && entry.method != 8) return false;
        if (entry.uncomp_size > max_bytes || entry.comp_size > max_bytes) return false;
        if (entry.local_offset >= file_size_) return false;

        ZipLocalHeader lfh{};
        if (!seek_abs(entry.local_offset) || !read_exact(&lfh, sizeof(lfh))) return false;
        if (lfh.signature != kSigLocal) return false;

        const uint64_t data_off = entry.local_offset + sizeof(ZipLocalHeader) + lfh.name_len + lfh.extra_len;
        if (data_off > file_size_) return false;
        if (!seek_abs(data_off)) return false;

        if (entry.method == 0) {
            if (entry.uncomp_size != entry.comp_size) return false;
            out.resize(static_cast<size_t>(entry.uncomp_size));
            if (entry.uncomp_size && !read_exact(out.data(), out.size())) {
                out.clear();
                return false;
            }
        } else {
            std::vector<uint8_t> comp(static_cast<size_t>(entry.comp_size));
            if (entry.comp_size && !read_exact(comp.data(), comp.size())) return false;
            out.resize(static_cast<size_t>(entry.uncomp_size));
            if (!inflate_raw(comp, out)) {
                out.clear();
                return false;
            }
        }

        if (entry.crc32 != 0 && crc32_bytes(out.data(), out.size()) != entry.crc32) {
            out.clear();
            return false;
        }
        return true;
    }

    bool decompress_entry_by_name(const std::string& inner_path, std::vector<uint8_t>& out, uint64_t max_bytes) {
        const EntryMeta* e = find_entry(inner_path);
        if (!e) return false;
        return decompress_entry(*e, out, max_bytes);
    }

    explicit ZipStream(const std::filesystem::path& path) : path_(path) {
#if defined(_WIN32) || defined(_WIN64)
        file_.open(make_win_long_path(path_).c_str(), std::ios::binary);
        if (!file_.is_open()) {
            file_.clear();
            file_.open(path_.wstring().c_str(), std::ios::binary);
        }
#else
        file_.open(path_, std::ios::binary);
#endif
        if (!file_.is_open()) return;
        parse_central_directory();
    }

    bool is_valid() const { return file_.is_open() && !entries_.empty(); }

    const std::vector<EntryMeta>& entries() const { return entries_; }

    const EntryMeta* find_entry(const std::string& inner_path) const {
        const std::string want = posix_norm(inner_path);
        for (const auto& e : entries_) {
            if (e.name == inner_path || e.name == want || ascii_ieq(e.name, inner_path) || ascii_ieq(e.name, want)) {
                return &e;
            }
        }
        return nullptr;
    }

    std::string manifest_json() {
        std::vector<std::string> html_files;
        std::vector<std::string> images;
        std::vector<std::string> css;

        for (const auto& e : entries_) {
            if (is_html_name(e.name)) html_files.push_back(e.name);
            else if (is_image_name(e.name)) images.push_back(e.name);
            else if (is_css_name(e.name)) css.push_back(e.name);
        }

        OpfPackage opf = load_opf_package();
        std::string opf_file = opf.opf_path;
        if (opf_file.empty()) {
            for (const auto& e : entries_) {
                if (has_ascii_suffix(e.name, ".opf")) {
                    opf_file = e.name;
                    break;
                }
            }
        }

        std::vector<std::string> spine;
        spine.reserve(opf.spine.size());
        for (const auto& href : opf.spine) {
            const EntryMeta* hit = find_entry(href);
            if (hit) spine.push_back(hit->name);
        }
        if (spine.empty()) spine = html_files;

        const std::string toc_file = pick_toc_file(opf);

        std::ostringstream oss;
        oss << "{\n"
            << "  \"status\": \"ok\",\n"
            << "  \"total_entries\": " << entries_.size() << ",\n"
            << "  \"opf_file\": " << json_str(opf_file) << ",\n"
            << "  \"toc_file\": " << json_str(toc_file) << ",\n"
            << "  \"title\": " << json_str(opf.title) << ",\n"
            << "  \"language\": " << json_str(opf.language.empty() ? "en" : opf.language) << ",\n"
            << "  \"spine\": [" << json_str_array(spine) << "],\n"
            << "  \"html\": [" << json_str_array(html_files) << "],\n"
            << "  \"images\": [" << json_str_array(images) << "],\n"
            << "  \"css\": [" << json_str_array(css) << "]\n"
            << "}\n";
        return oss.str();
    }

    void print_manifest() {
        std::cout << manifest_json();
    }

    bool read_entry(const std::string& inner_path, std::vector<uint8_t>& out) {
        return decompress_entry_by_name(inner_path, out, kMaxSingleBytes);
    }

    bool stream_single(const std::string& inner_path) {
        std::vector<uint8_t> buffer;
        if (!read_entry(inner_path, buffer)) return false;
        SET_BINARY_MODE(stdout);
        if (!buffer.empty()) {
            std::cout.write(reinterpret_cast<const char*>(buffer.data()), static_cast<std::streamsize>(buffer.size()));
        }
        std::cout.flush();
        return true;
    }

    bool build_html_frames(std::vector<uint8_t>& out) {
        out.clear();
        std::vector<uint8_t> buffer;
        uint64_t total = 0;
        for (const auto& e : entries_) {
            if (!is_html_name(e.name) || e.uncomp_size == 0) continue;
            if (!decompress_entry(e, buffer, kMaxHtmlEntryBytes)) continue;
            if (buffer.size() > std::numeric_limits<uint32_t>::max() ||
                e.name.size() > std::numeric_limits<uint32_t>::max()) {
                continue;
            }
            const uint32_t name_len = static_cast<uint32_t>(e.name.size());
            const uint32_t data_len = static_cast<uint32_t>(buffer.size());
            const uint64_t next = total + 8ull + name_len + data_len;
            if (next > kMaxHtmlStreamBytes) break;
            const size_t pos = out.size();
            out.resize(pos + 8 + name_len + data_len);
            std::memcpy(out.data() + pos, &name_len, 4);
            std::memcpy(out.data() + pos + 4, e.name.data(), name_len);
            std::memcpy(out.data() + pos + 4 + name_len, &data_len, 4);
            std::memcpy(out.data() + pos + 8 + name_len, buffer.data(), data_len);
            total = next;
        }
        return true;
    }

    bool stream_html_frames() {
        std::vector<uint8_t> framed;
        if (!build_html_frames(framed)) return false;
        SET_BINARY_MODE(stdout);
        if (!framed.empty()) {
            std::cout.write(reinterpret_cast<const char*>(framed.data()), static_cast<std::streamsize>(framed.size()));
        }
        std::cout.flush();
        return true;
    }

    PeekStats peek_stats(const std::string& mode) {
        PeekStats stats{mode};
        const auto start = std::chrono::high_resolution_clock::now();
        std::vector<uint8_t> buffer;

        for (const auto& e : entries_) {
            bool match = false;
            if (mode == "html") match = is_html_name(e.name);
            else if (mode == "css") match = is_css_name(e.name);
            else if (mode == "image") match = is_image_name(e.name);
            else if (mode == "all") match = true;
            if (!match || e.uncomp_size == 0) continue;
            const uint64_t cap = is_html_name(e.name) ? kMaxHtmlEntryBytes : kMaxSingleBytes;
            if (decompress_entry(e, buffer, cap)) {
                stats.count++;
                stats.total_bytes += buffer.size();
                stats.xor_crc ^= e.crc32;
            }
        }
        const auto end = std::chrono::high_resolution_clock::now();
        stats.elapsed_ms = std::chrono::duration<double, std::milli>(end - start).count();
        return stats;
    }
};

static int zipstream_alloc_copy(const void* data, size_t n, char** out, int* out_len) {
    if (!out || !out_len) return 1;
    *out = nullptr;
    *out_len = 0;
    if (n > static_cast<size_t>(std::numeric_limits<int>::max())) return 5;
    char* buf = static_cast<char*>(std::malloc(n ? n : 1));
    if (!buf) return 1;
    if (n && data) std::memcpy(buf, data, n);
    *out = buf;
    *out_len = static_cast<int>(n);
    return 0;
}

template <typename Fn>
static int zipstream_guard(Fn&& fn) {
    try {
        return fn();
    } catch (...) {
        return 99;
    }
}

ZIPSTREAM_API int zipstream_manifest(const char* epub_path, char** out, int* out_len) {
    return zipstream_guard([&]() {
        if (!epub_path) return 1;
        ZipStream zs(path_from_utf8(epub_path));
        if (!zs.is_valid()) return 2;
        const std::string json = zs.manifest_json();
        return zipstream_alloc_copy(json.data(), json.size(), out, out_len);
    });
}

ZIPSTREAM_API int zipstream_single(const char* epub_path, const char* inner_path, char** out, int* out_len) {
    return zipstream_guard([&]() {
        if (!epub_path || !inner_path) return 1;
        ZipStream zs(path_from_utf8(epub_path));
        if (!zs.is_valid()) return 2;
        std::vector<uint8_t> buffer;
        if (!zs.read_entry(inner_path, buffer)) return 3;
        return zipstream_alloc_copy(buffer.data(), buffer.size(), out, out_len);
    });
}

ZIPSTREAM_API int zipstream_stream_html(const char* epub_path, char** out, int* out_len) {
    return zipstream_guard([&]() {
        if (!epub_path) return 1;
        ZipStream zs(path_from_utf8(epub_path));
        if (!zs.is_valid()) return 2;
        std::vector<uint8_t> framed;
        if (!zs.build_html_frames(framed)) return 4;
        return zipstream_alloc_copy(framed.data(), framed.size(), out, out_len);
    });
}

ZIPSTREAM_API void zipstream_free(void* p) {
    std::free(p);
}

#ifndef ZIPSTREAM_DLL
int main(int argc, char** argv) {
    std::string command;
    std::filesystem::path epub_path;
    std::string path_utf8;
    std::string extra;

#if defined(_WIN32) || defined(_WIN64)
    (void)argc;
    (void)argv;
    auto wargs = win_wide_args();
    if (wargs.size() < 3) {
        std::cerr << "Usage:\n"
                  << "  zipstream manifest <epub_path>\n"
                  << "  zipstream single <epub_path> <inner_path>\n"
                  << "  zipstream stream_html <epub_path>\n"
                  << "  zipstream stats <html|css|image|all> <epub_path>\n";
        return 1;
    }
    command = wide_to_utf8(wargs[1]);
    if (command == "stats") {
        if (wargs.size() < 4) {
            std::cerr << "Error: Missing stats mode (html|css|image|all)\n";
            return 1;
        }
        extra = wide_to_utf8(wargs[2]);
        epub_path = wargs[3];
        path_utf8 = wide_to_utf8(wargs[3]);
    } else {
        epub_path = wargs[2];
        path_utf8 = wide_to_utf8(wargs[2]);
        extra = (wargs.size() >= 4) ? wide_to_utf8(wargs[3]) : "";
    }
#else
    if (argc < 3) {
        std::cerr << "Usage:\n"
                  << "  " << argv[0] << " manifest <epub_path>\n"
                  << "  " << argv[0] << " single <epub_path> <inner_path>\n"
                  << "  " << argv[0] << " stream_html <epub_path>\n"
                  << "  " << argv[0] << " stats <html|css|image|all> <epub_path>\n";
        return 1;
    }
    command = argv[1];
    if (command == "stats") {
        if (argc < 4) {
            std::cerr << "Error: Missing stats mode (html|css|image|all)\n";
            return 1;
        }
        extra = argv[2];
        epub_path = argv[3];
        path_utf8 = argv[3];
    } else {
        epub_path = argv[2];
        path_utf8 = argv[2];
        extra = (argc >= 4) ? argv[3] : "";
    }
#endif

    try {
        ZipStream zs(epub_path);
        if (!zs.is_valid()) {
            std::cerr << "Error: Could not open or parse EPUB: " << path_utf8 << "\n";
            return 2;
        }

        if (command == "manifest") {
            zs.print_manifest();
            return 0;
        } else if (command == "single") {
            if (extra.empty()) {
                std::cerr << "Error: Missing inner_path argument for single mode\n";
                return 1;
            }
            if (!zs.stream_single(extra)) {
                std::cerr << "Error: Could not decompress entry: " << extra << "\n";
                return 3;
            }
            return 0;
        } else if (command == "stream_html") {
            if (!zs.stream_html_frames()) {
                std::cerr << "Error: Failed streaming HTML frames\n";
                return 4;
            }
            return 0;
        } else if (command == "stats") {
            PeekStats s = zs.peek_stats(extra);
            std::cout << "{\"mode\": " << json_str(s.mode)
                      << ", \"count\": " << s.count
                      << ", \"total_bytes\": " << s.total_bytes
                      << ", \"xor_crc\": " << s.xor_crc
                      << ", \"time_ms\": " << s.elapsed_ms << "}\n";
            return 0;
        }

        std::cerr << "Unknown command: " << command << "\n";
        return 1;
    } catch (...) {
        std::cerr << "Error: zipstream aborted while reading " << path_utf8 << "\n";
        return 99;
    }
}
#endif
