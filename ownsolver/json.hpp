// Minimal JSON reader (objects, arrays, numbers, strings, true/false/null).
#pragma once
#include <cstring>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

struct JV {
    enum T { NUL, NUM, STR, ARR, OBJ, BOOL } t = NUL;
    double num = 0;
    bool b = false;
    std::string str;
    std::vector<JV> arr;
    std::map<std::string, JV> obj;
    bool has(const std::string& k) const { return t == OBJ && obj.count(k); }
    const JV& operator[](const std::string& k) const {
        auto it = obj.find(k);
        if (it == obj.end()) throw std::runtime_error("missing key " + k);
        return it->second;
    }
    const JV& operator[](size_t i) const { return arr.at(i); }
    size_t size() const { return arr.size(); }
    long long i() const { return (long long)num; }
};

struct JParser {
    const std::string& s;
    size_t p = 0;
    explicit JParser(const std::string& s_) : s(s_) {}
    void ws() { while (p < s.size() && isspace((unsigned char)s[p])) p++; }
    JV parse() {
        ws();
        if (p >= s.size()) throw std::runtime_error("json: unexpected end");
        char c = s[p];
        JV v;
        if (c == '{') {
            v.t = JV::OBJ; p++; ws();
            if (s[p] == '}') { p++; return v; }
            while (true) {
                ws(); JV k = parse(); ws();
                if (s[p] != ':') throw std::runtime_error("json: expected :");
                p++;
                v.obj[k.str] = parse(); ws();
                if (s[p] == ',') { p++; continue; }
                if (s[p] == '}') { p++; return v; }
                throw std::runtime_error("json: expected , or }");
            }
        }
        if (c == '[') {
            v.t = JV::ARR; p++; ws();
            if (s[p] == ']') { p++; return v; }
            while (true) {
                v.arr.push_back(parse()); ws();
                if (s[p] == ',') { p++; continue; }
                if (s[p] == ']') { p++; return v; }
                throw std::runtime_error("json: expected , or ]");
            }
        }
        if (c == '"') {
            v.t = JV::STR; p++;
            while (s[p] != '"') {
                if (s[p] == '\\') { p++; }
                v.str += s[p++];
            }
            p++;
            return v;
        }
        if (s.compare(p, 4, "null") == 0) { p += 4; return v; }
        if (s.compare(p, 4, "true") == 0) { p += 4; v.t = JV::BOOL; v.b = true; return v; }
        if (s.compare(p, 5, "false") == 0) { p += 5; v.t = JV::BOOL; return v; }
        size_t q = p;
        while (q < s.size() && (isdigit((unsigned char)s[q]) || strchr("+-.eE", s[q]))) q++;
        if (q == p) throw std::runtime_error("json: bad token");
        v.t = JV::NUM; v.num = std::stod(s.substr(p, q - p)); p = q;
        return v;
    }
};
