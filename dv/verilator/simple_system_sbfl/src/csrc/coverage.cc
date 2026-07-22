#include <unistd.h>

#include <charconv>
#include <cstddef>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#if VM_COVERAGE == 1
#include "coverage.h"
#include "verilated.h"
#include "verilated_cov.h"
#include "verilated_cov_key.h"
#include "verilated_toplevel.h"
#endif  // VM_COVERAGE

#if VM_COVERAGE == 1

static std::optional<std::pair<std::string_view, std::string_view>>
verilator_coverage_parse_field(const std::string_view field) {
  auto sep = field.find('\002');
  if (sep == std::string_view::npos) {
    return std::nullopt;
  }
  return std::pair{
      field.substr(0, sep),
      field.substr(sep + 1),
  };
}

static std::optional<VerilatorCoverPoint> verilator_coverage_parse_line(
    const std::string_view line) {
  if (line.rfind("C '", 0) != 0) {
    return std::nullopt;
  }

  auto metadata_begin = line.find('\'');
  if (metadata_begin == std::string_view::npos) {
    return std::nullopt;
  }
  auto metadata_end = line.find('\'', metadata_begin + 1);
  if (metadata_end == std::string_view::npos) {
    return std::nullopt;
  }

  VerilatorCoverPoint cover_point{};
  auto metadata =
      line.substr(metadata_begin + 1, metadata_end - metadata_begin - 1);
  auto count_str = line.substr(metadata_end + 1);
  while (!count_str.empty() && std::isspace(count_str.front())) {
    count_str.remove_prefix(1);
  }
  while (!count_str.empty() && std::isspace(count_str.back())) {
    count_str.remove_suffix(1);
  }
  if (auto [ptr, ec] =
          std::from_chars(count_str.data(), count_str.data() + count_str.size(),
                          cover_point.count);
      ptr != count_str.data() + count_str.size() || ec != std::errc()) {
    return std::nullopt;
  }

  size_t pos = 0;
  while (pos < metadata.size()) {
    auto next = metadata.find('\001', pos);
    auto field = metadata.substr(
        pos, next == std::string_view::npos ? next : next - pos);
    if (auto parsed = verilator_coverage_parse_field(field)) {
      auto [key, value] = parsed.value();
      if (key == VL_CIK_FILENAME) {
        cover_point.filename = value;
      } else if (key == VL_CIK_LINENO) {
        cover_point.lineno = value;
      } else if (key == VL_CIK_COLUMN) {
        cover_point.column = value;
      } else if (key == VL_CIK_TYPE) {
        cover_point.type = value;
      } else if (key == VL_CIK_LINESCOV) {
        cover_point.linescov = value;
      } else if (key == VL_CIK_COMMENT) {
        cover_point.comment = value;
      } else if (key == VL_CIK_HIER) {
        cover_point.hier = value;
      }
    }
    if (next == std::string_view::npos) {
      break;
    }
    pos = next + 1;
  }

  if (cover_point.type.empty()) {
    return std::nullopt;
  }

  cover_point.gen_name();
  return cover_point;
}

VerilatorCoverage::VerilatorCoverage()
    : cover{VerilatorCoverGroup("line"), VerilatorCoverGroup("branch"),
            VerilatorCoverGroup("expr"), VerilatorCoverGroup("toggle")} {
  ibex_simple_system tmp;
  PostExec();
}

void VerilatorCoverage::reset() {
  for (auto &group : cover) {
    for (auto &point : group.points) {
      point.count = 0;
    }
  }
  VerilatedCov::clear();
}

const char *VerilatorCoverage::get_cover_name(uint32_t i) {
  if (auto group = get(); group && i < group->points.size()) {
    return group->points[i].name.c_str();
  }
  return nullptr;
}

uint32_t VerilatorCoverage::get_total_points() {
  if (auto group = get()) {
    return group->points.size();
  }
  return 0;
}

uint32_t VerilatorCoverage::get_covered_points() { return cover_sum(); }

void VerilatorCoverage::update_is_feedback(const char *cover_name) {
  auto name_len = strlen(get_name());
  auto cmp = cover_name_cmp(cover_name, get_name());
  is_feedback = cmp > name_len || !cmp;
  feedback_group = -1;
  if (is_feedback && cover_name[name_len]) {
    // skip the name and dot (.)
    bool found = false;
    auto subname = cover_name + name_len + 1;
    for (int i = 0; i < cover.size(); ++i) {
      if (!cover_name_cmp(subname, cover[i].name.c_str())) {
        feedback_group = i;
        found = true;
      }
    }
    if (!found) {
      std::cerr << "Unknown subtype of VerilatorCoverage: " << cover_name
                << std::endl;
      assert(found);
    }
  }
}

size_t VerilatorCoverage::cover_data_size() { return sizeof(uint64_t); }

void VerilatorCoverage::to_cover_data(void *data) {
  if (auto group = get()) {
    for (size_t i = 0; i < group->points.size(); ++i) {
      ((uint64_t *)data)[i] = group->points[i].count;
    }
  }
}

void VerilatorCoverage::display() {
  for (const auto &group : cover) {
    display(group);
  }
}

void VerilatorCoverage::display_uncovered_points() {
  for (const auto &group : cover) {
    if (!group.points.empty()) {
      std::cout << "Uncovered " << group.name
                << " coverage points:" << std::endl;
      for (size_t i = 0; i < group.points.size(); i++) {
        std::cout << "  [" << i << "] " << group.points[i].name << std::endl;
      }
    }
  }
}

void VerilatorCoverage::PreExec() { VerilatedCov::zero(); }

void VerilatorCoverage::PostExec() {
  char filename[] = "/tmp/verilator-coverage-XXXXXX";
  auto fd = mkstemp(filename);
  if (fd < 0) {
    std::cerr << "Failed to create temporary file for Verilator coverage"
              << std::endl;
    assert(fd >= 0);
  }
  close(fd);
  VerilatedCov::write(filename);
  VerilatedCov::clear();
  load_from_file(filename);
  unlink(filename);
}

void VerilatorCoverage::load_from_file(const char *filename) {
  std::vector<VerilatorCoverPoint> line{};
  std::vector<VerilatorCoverPoint> branch{};
  std::vector<VerilatorCoverPoint> expr{};
  std::vector<VerilatorCoverPoint> toggle{};

  std::ifstream input(filename);
  std::string text;
  while (std::getline(input, text)) {
    std::string type{}, point_name{};
    auto cover_point = verilator_coverage_parse_line(text);
    if (!cover_point) {
      continue;
    }

    if (cover_point->type == "line") {
      line.emplace_back(cover_point.value());
    } else if (cover_point->type == "expr") {
      expr.emplace_back(cover_point.value());
    } else if (cover_point->type == "branch") {
      branch.emplace_back(cover_point.value());
    } else if (cover_point->type == "toggle") {
      toggle.emplace_back(cover_point.value());
    } else {
      printf("Unknown coverage type from Verilator: %s\n", type.c_str());
      assert(0);
    }
  }

  update_group("line", line);
  update_group("branch", branch);
  update_group("expr", expr);
  update_group("toggle", toggle);
}

void VerilatorCoverage::update_group(const std::string &type,
                                     std::vector<VerilatorCoverPoint> &points) {
  auto group = get(type);
  assert(group);

  if (!group->points.empty()) {
    if (group->points.size() != points.size()) {
      std::cerr << "Point num mismatch for Verilator coverage group " << type
                << ", expected: " << group->points.size()
                << ", but get: " << points.size() << std::endl;
      assert(group->points.size() == points.size());
    }
    for (size_t i = 0; i < points.size(); i++) {
      if (group->points[i].name != points[i].name) {
        std::cerr << "Point name mismatch for Verilator coverage group " << type
                  << " at index " << i << ": " << group->points[i].name
                  << " vs " << points[i].name << std::endl;
        assert(group->points[i].name == points[i].name);
      }
    }
  }

  group->points = std::move(points);
}

VerilatorCoverGroup *VerilatorCoverage::get() {
  if (feedback_group < 0) {
    return nullptr;
  }
  return &cover[feedback_group];
}

VerilatorCoverGroup *VerilatorCoverage::get(const std::string &type) {
  for (auto &group : cover) {
    if (group.name == type) {
      return &group;
    }
  }
  return nullptr;
}

uint32_t VerilatorCoverage::cover_sum(const VerilatorCoverGroup &group) {
  uint32_t result = 0;
  for (const auto &point : group.points) {
    result += point.count != 0;
  }
  return result;
}

uint32_t VerilatorCoverage::cover_sum() {
  if (auto group = get()) {
    return cover_sum(*group);
  }
  return 0;
}

void VerilatorCoverage::display(const VerilatorCoverGroup &group) {
  std::cout << "COVERAGE: " << ("verilator." + group.name) << ", "
            << group.points.size() << ", " << cover_sum(group) << std::endl;
}

#endif  // VM_COVERAGE
