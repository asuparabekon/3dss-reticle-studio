// Offscreen D3D11 host for the game's MSDF and glow shaders. No engine DLLs.
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <array>
#include <algorithm>
#include <cmath>
#include <initializer_list>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <io.h>

using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;

static void check(HRESULT result, const char* operation)
{
    if (FAILED(result))
        throw std::runtime_error(std::string(operation) + ": " + std::to_string(unsigned(result)));
}

static std::vector<char> read_file(const fs::path& path)
{
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("Cannot read " + path.string());
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}

struct Includes : ID3DInclude
{
    fs::path root;
    explicit Includes(fs::path directory) : root(std::move(directory)) {}
    HRESULT Open(D3D_INCLUDE_TYPE, LPCSTR name, LPCVOID, LPCVOID* data, UINT* bytes) override
    {
        try {
            auto file = read_file(root / name);
            auto memory = new char[file.size()];
            memcpy(memory, file.data(), file.size());
            *data = memory; *bytes = UINT(file.size());
            return S_OK;
        } catch (...) { return E_FAIL; }
    }
    HRESULT Close(LPCVOID data) override { delete[] static_cast<const char*>(data); return S_OK; }
};

struct Input
{
    uint32_t width, height, mip, game_height;
    float mapping[4]; // units/pixel, scope, background grey, illumination on
    float timing[4];  // game tonemap exposure, time, eye x mm, authored glow size
    float colour[4];
    float params[4];  // player holo level, brightness, grain, line weight
    float weights[4]; // tight, mid, wide, white shoulder
    float tint[4];    // tint, exposure power, halo ceiling, unused
    float exposure[4]; // game middle grey, amount, game floor, glow floor
    float sigmas[4]; // engine lobe sigma at 1080p: tight, mid, wide
    float view[4]; // preview pan in pixels x/y, readback width/height
};
static_assert(sizeof(Input) == 4 * sizeof(uint32_t) + 36 * sizeof(float), "Preview packet layout changed");

struct Texture
{
    ComPtr<ID3D11Texture2D> texture;
    ComPtr<ID3D11ShaderResourceView> srv;
    ComPtr<ID3D11RenderTargetView> rtv;
    UINT width = 0, height = 0;
};

struct Shader
{
    ComPtr<ID3D11PixelShader> shader;
    ComPtr<ID3D11Buffer> constants;
    ComPtr<ID3D11ShaderReflection> reflection;
    std::vector<char> data;
    UINT slot = 0;
    std::map<std::string, UINT, std::less<>> resources, samplers;
    std::map<std::string, std::pair<UINT, UINT>, std::less<>> variables; // offset and copy size in $Globals

    void set(const char* name, const float* value)
    {
        const auto variable = variables.find(name);
        if (variable != variables.end())
            memcpy(data.data() + variable->second.first, value, variable->second.second);
    }
};

class Renderer
{
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11VertexShader> vertex;
    ComPtr<ID3D11SamplerState> linear, wrap;
    ComPtr<ID3D11BlendState> blend;
    Shader reticle, down, blur, combine;
    Texture atlas, scene, core, source, tight, half[2], quarter[2], tonemap;
    ComPtr<ID3D11Texture2D> staging;
    fs::path atlas_path;
    fs::file_time_type atlas_time{};
    UINT atlas_mip = ~0u, readback_width = 0, readback_height = 0;
    fs::path shaders;
    UINT current_width = 0, current_height = 0;

    ComPtr<ID3DBlob> compile(const std::string& text, const char* name, const char* target)
    {
        ComPtr<ID3DBlob> result, errors;
        Includes includes(shaders);
        // X-Ray's compiler: row-major, default optimization, no IEEE strictness.
        HRESULT hr = D3DCompile(text.data(), text.size(), name, nullptr, &includes, "main", target,
                               D3DCOMPILE_PACK_MATRIX_ROW_MAJOR, 0, &result, &errors);
        if (errors) std::cerr.write(static_cast<char*>(errors->GetBufferPointer()), errors->GetBufferSize());
        check(hr, name);
        return result;
    }

    Shader pixel(const char* name)
    {
        ComPtr<ID3DBlob> code;
        const fs::path compiled = shaders / (std::string(name) + ".cso");
        if (fs::exists(compiled)) {
            auto bytes = read_file(compiled);
            check(D3DCreateBlob(bytes.size(), &code), "Load compiled shader");
            memcpy(code->GetBufferPointer(), bytes.data(), bytes.size());
        } else {
            auto bytes = read_file(shaders / name);
            code = compile(std::string(bytes.begin(), bytes.end()), name, "ps_5_0");
        }
        Shader output;
        check(device->CreatePixelShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &output.shader), name);
        check(D3DReflect(code->GetBufferPointer(), code->GetBufferSize(), IID_PPV_ARGS(&output.reflection)), "Reflect shader");
        D3D11_SHADER_DESC shader_desc{};
        output.reflection->GetDesc(&shader_desc);
        for (UINT i = 0; i < shader_desc.BoundResources; ++i) {
            D3D11_SHADER_INPUT_BIND_DESC binding{};
            output.reflection->GetResourceBindingDesc(i, &binding);
            if (binding.Type == D3D_SIT_TEXTURE) output.resources[binding.Name] = binding.BindPoint;
            if (binding.Type == D3D_SIT_SAMPLER) output.samplers[binding.Name] = binding.BindPoint;
            if (binding.Type == D3D_SIT_CBUFFER && std::string(binding.Name) == "$Globals") output.slot = binding.BindPoint;
        }
        D3D11_SHADER_BUFFER_DESC desc{};
        if (SUCCEEDED(output.reflection->GetConstantBufferByName("$Globals")->GetDesc(&desc))) {
            auto globals = output.reflection->GetConstantBufferByName("$Globals");
            for (UINT i = 0; i < desc.Variables; ++i) {
                D3D11_SHADER_VARIABLE_DESC variable{};
                if (SUCCEEDED(globals->GetVariableByIndex(i)->GetDesc(&variable)))
                    output.variables.emplace(variable.Name,
                        std::make_pair(variable.StartOffset, std::min(UINT(16), variable.Size)));
            }
            output.data.resize((desc.Size + 15) & ~15u);
            D3D11_BUFFER_DESC buffer{};
            buffer.ByteWidth = UINT(output.data.size()); buffer.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
            check(device->CreateBuffer(&buffer, nullptr, &output.constants), "Create constants");
        }
        return output;
    }

    Texture target(UINT width, UINT height, DXGI_FORMAT format = DXGI_FORMAT_R16G16B16A16_FLOAT)
    {
        Texture t; t.width = width; t.height = height;
        D3D11_TEXTURE2D_DESC desc{};
        desc.Width = width; desc.Height = height; desc.MipLevels = desc.ArraySize = 1;
        desc.Format = format; desc.SampleDesc.Count = 1;
        desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
        check(device->CreateTexture2D(&desc, nullptr, &t.texture), "Create target");
        check(device->CreateShaderResourceView(t.texture.Get(), nullptr, &t.srv), "Create target SRV");
        check(device->CreateRenderTargetView(t.texture.Get(), nullptr, &t.rtv), "Create target RTV");
        return t;
    }

    void bind(Shader& shader, std::initializer_list<std::pair<const char*, Texture*>> textures)
    {
        ID3D11ShaderResourceView* empty[16]{};
        context->PSSetShaderResources(0, 16, empty);
        context->PSSetShader(shader.shader.Get(), nullptr, 0);
        if (shader.constants) {
            context->UpdateSubresource(shader.constants.Get(), 0, nullptr, shader.data.data(), 0, 0);
            ID3D11Buffer* cb = shader.constants.Get();
            context->PSSetConstantBuffers(shader.slot, 1, &cb);
        }
        for (auto& entry : textures) {
            auto found = shader.resources.find(entry.first);
            if (found != shader.resources.end()) {
                ID3D11ShaderResourceView* view = entry.second->srv.Get();
                context->PSSetShaderResources(found->second, 1, &view);
            }
        }
        for (auto& entry : shader.samplers) {
            ID3D11SamplerState* sampler = entry.first == "smp_linear" ? wrap.Get() : linear.Get();
            context->PSSetSamplers(entry.second, 1, &sampler);
        }
    }

    void viewport(UINT width, UINT height)
    {
        D3D11_VIEWPORT view{0, 0, float(width), float(height), 0, 1};
        context->RSSetViewports(1, &view);
    }

    void pass(Shader& shader, Texture& output, Texture& input, float dx, float dy, float sigma)
    {
        context->OMSetRenderTargets(0, nullptr, nullptr);
        float parameters[]{dx, dy, sigma, 0};
        shader.set("reticle_glow_blur", parameters);
        bind(shader, {{"s_glow_source", &input}});
        ID3D11RenderTargetView* rtv = output.rtv.Get();
        context->OMSetRenderTargets(1, &rtv, nullptr);
        context->OMSetBlendState(nullptr, nullptr, ~0u);
        viewport(output.width, output.height);
        context->Draw(3, 0);
    }

    void load_atlas(const fs::path& path, UINT first)
    {
        auto bytes = read_file(path);
        if (bytes.size() < 128 || memcmp(bytes.data(), "DDS ", 4)) throw std::runtime_error("Not DDS");
        auto integer = [&](size_t offset) { uint32_t v; memcpy(&v, bytes.data() + offset, 4); return v; };
        UINT height = integer(12), width = integer(16), mips = integer(28);
        if (integer(4) != 124 || integer(76) != 32 || integer(112) != 0 ||
            width == 0 || height == 0 || width > D3D11_REQ_TEXTURE2D_U_OR_V_DIMENSION || height > D3D11_REQ_TEXTURE2D_U_OR_V_DIMENSION)
            throw std::runtime_error("Invalid DDS dimensions or header");
        UINT maximum_mips = 1;
        for (UINT side = std::max(width, height); side > 1; side >>= 1) ++maximum_mips;
        if (mips == 0 || mips > maximum_mips) throw std::runtime_error("Invalid DDS mip count");
        bool bc5 = integer(84) == 0x30315844;
        if (bc5) {
            if (bytes.size() < 148 || integer(128) != 83 || integer(132) != 3 || integer(136) != 0 || integer(140) != 1)
                throw std::runtime_error("Unsupported DDS format");
        } else if (integer(80) != 0x41 || integer(84) != 0 || integer(88) != 32 ||
                   integer(92) != 0xff || integer(96) != 0xff00 || integer(100) != 0xff0000 || integer(104) != 0xff000000) {
            throw std::runtime_error("Expected RGBA8 or BC5 DDS");
        }
        if (first >= mips) throw std::runtime_error("Resident mip out of range");
        size_t offset = bc5 ? 148 : 128;
        std::vector<D3D11_SUBRESOURCE_DATA> data;
        for (UINT m = 0; m < mips; ++m) {
            UINT w = std::max(width >> m, 1u), h = std::max(height >> m, 1u);
            UINT pitch = bc5 ? ((w + 3) / 4) * 16 : w * 4;
            UINT size = pitch * (bc5 ? ((h + 3) / 4) : h);
            if (offset + size > bytes.size()) throw std::runtime_error("DDS truncated");
            if (m >= first) data.push_back({bytes.data() + offset, pitch, size});
            offset += size;
        }
        if (offset != bytes.size()) throw std::runtime_error("DDS size mismatch");
        D3D11_TEXTURE2D_DESC desc{};
        desc.Width = std::max(width >> first, 1u); desc.Height = std::max(height >> first, 1u);
        desc.MipLevels = UINT(data.size()); desc.ArraySize = 1; desc.SampleDesc.Count = 1;
        desc.Format = bc5 ? DXGI_FORMAT_BC5_UNORM : DXGI_FORMAT_R8G8B8A8_UNORM;
        desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        atlas = {};
        check(device->CreateTexture2D(&desc, data.data(), &atlas.texture), "Upload DDS");
        check(device->CreateShaderResourceView(atlas.texture.Get(), nullptr, &atlas.srv), "Atlas SRV");
    }

public:
    explicit Renderer(fs::path folder) : shaders(std::move(folder))
    {
        D3D_FEATURE_LEVEL feature = D3D_FEATURE_LEVEL_11_0;
        check(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, &feature, 1, D3D11_SDK_VERSION,
                               &device, nullptr, &context), "Create D3D11 device");
        const char* vs = "struct O{float4 p:SV_Position;float2 uv:TEXCOORD0;}; O main(uint i:SV_VertexID){"
                         "O o;float2 uv=float2((i<<1)&2,i&2);o.p=float4(uv*float2(2,-2)+float2(-1,1),0,1);o.uv=uv;return o;}";
        auto code = compile(vs, "preview vertex", "vs_5_0");
        check(device->CreateVertexShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &vertex), "Create VS");
        context->VSSetShader(vertex.Get(), nullptr, 0);
        context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        D3D11_RASTERIZER_DESC raster{}; raster.FillMode = D3D11_FILL_SOLID; raster.CullMode = D3D11_CULL_NONE;
        ComPtr<ID3D11RasterizerState> rs;
        check(device->CreateRasterizerState(&raster, &rs), "Raster state"); context->RSSetState(rs.Get());
        D3D11_SAMPLER_DESC sampler{};
        sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        sampler.MaxLOD = D3D11_FLOAT32_MAX;
        check(device->CreateSamplerState(&sampler, &linear), "Clamp sampler");
        sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_WRAP;
        check(device->CreateSamplerState(&sampler, &wrap), "Wrap sampler");
        D3D11_BLEND_DESC blending{};
        auto& b = blending.RenderTarget[0]; b.BlendEnable = TRUE;
        b.SrcBlend = b.SrcBlendAlpha = D3D11_BLEND_ONE;
        b.DestBlend = b.DestBlendAlpha = D3D11_BLEND_INV_SRC_ALPHA;
        b.BlendOp = b.BlendOpAlpha = D3D11_BLEND_OP_ADD;
        b.RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
        check(device->CreateBlendState(&blending, &blend), "Game blend state");
        reticle = pixel("preview_reticle.ps"); down = pixel("reticle_glow_down.ps");
        blur = pixel("reticle_glow_blur.ps"); combine = pixel("reticle_emission_combine.ps");
        tonemap = target(1, 1, DXGI_FORMAT_R32G32B32A32_FLOAT);
    }

    std::vector<char> render(const Input& input, const fs::path& path)
    {
        if (input.width < 16 || input.height < 16 || input.width > 4096 || input.height > 4096)
            throw std::runtime_error("Invalid frame size");
        context->OMSetRenderTargets(0, nullptr, nullptr);
        ID3D11ShaderResourceView* empty[16]{}; context->PSSetShaderResources(0, 16, empty);
        const auto modified = fs::last_write_time(path);
        if (path != atlas_path || modified != atlas_time || input.mip != atlas_mip) {
            load_atlas(path, input.mip);
            atlas_path = path; atlas_time = modified; atlas_mip = input.mip;
        }
        if (current_width != input.width || current_height != input.height) {
            current_width = input.width; current_height = input.height;
            scene = target(input.width, input.height, DXGI_FORMAT_R8G8B8A8_UNORM);
            core = target(input.width, input.height); source = target(input.width, input.height);
            tight = target(input.width, input.height);
            for (int i = 0; i < 2; ++i) {
                half[i] = target(input.width / 2, input.height / 2);
                quarter[i] = target(input.width / 4, input.height / 4);
            }
        }
        const float params[]{input.params[0], input.params[1], input.params[2], input.params[3]};
        const float control[]{1, 0, 1, 1}, reflex[]{1, 0, 0, 0};
        const float frame[]{float(input.width), float(input.height), input.mapping[0], input.mapping[1]};
        const float scene_settings[]{input.mapping[2], input.mapping[3], input.view[0], input.view[1]};
        const float time[]{input.timing[1], 0, 0, 0}, eye[]{input.timing[2] / 1000, 0, 0, 1};
        reticle.set("reticle_msdf_params", params); reticle.set("reticle_msdf_control", control);
        reticle.set("reticle_msdf_reflex_glow", reflex); reticle.set("preview_frame", frame);
        reticle.set("preview_scene", scene_settings); reticle.set("timers", time); reticle.set("reticle_holo_eye", eye);
        reticle.set("markswitch_color", input.colour);
        // mark is supplied in colour alpha; it is not a dot-offset in this fixture.
        const float mark[]{input.colour[3], 0, 0, 0}; reticle.set("markswitch_current", mark);
        bind(reticle, {{"s_base", &atlas}});
        ID3D11RenderTargetView* outputs[]{scene.rtv.Get(), core.rtv.Get(), source.rtv.Get()};
        context->OMSetRenderTargets(3, outputs, nullptr); context->OMSetBlendState(nullptr, nullptr, ~0u);
        viewport(input.width, input.height); context->Draw(3, 0);
        float px = float(input.game_height) / 1080 * input.timing[3];
        pass(down, half[0], source, 0, 0, 0);
        pass(down, quarter[0], half[0], 0, 0, 0);
        pass(blur, quarter[1], quarter[0], 1, 0, input.sigmas[2] * px / 4);
        pass(blur, quarter[0], quarter[1], 0, 1, input.sigmas[2] * px / 4);
        pass(blur, half[1], half[0], 1, 0, input.sigmas[1] * px / 2);
        pass(blur, half[0], half[1], 0, 1, input.sigmas[1] * px / 2);
        pass(blur, tight, source, 1, 0, input.sigmas[0] * px);
        context->OMSetRenderTargets(0, nullptr, nullptr);
        const float exposure[]{input.timing[0], 0, 0, 0};
        context->UpdateSubresource(tonemap.texture.Get(), 0, nullptr, exposure, 16, 16);
        const float tint[]{input.tint[0], input.sigmas[0] * px, input.tint[1], input.tint[2]};
        combine.set("reticle_glow_weight", input.weights); combine.set("reticle_glow_tint", tint);
        combine.set("reticle_glow_exposure", input.exposure);
        bind(combine, {{"s_reticle_emission", &core}, {"s_glow_tight", &tight}, {"s_glow_mid", &half[0]},
                       {"s_glow_wide", &quarter[0]}, {"s_tonemap", &tonemap}});
        ID3D11RenderTargetView* output = scene.rtv.Get(); context->OMSetRenderTargets(1, &output, nullptr);
        context->OMSetBlendState(blend.Get(), nullptr, ~0u); viewport(input.width, input.height); context->Draw(3, 0);
        context->OMSetRenderTargets(0, nullptr, nullptr);
        // Render the unchanged full-frame graph; transfer only the inspected crop.
        const UINT width = std::min(UINT(input.view[2]), input.width);
        const UINT height = std::min(UINT(input.view[3]), input.height);
        if (readback_width != width || readback_height != height) {
            D3D11_TEXTURE2D_DESC desc{}; scene.texture->GetDesc(&desc);
            desc.Width = width; desc.Height = height;
            desc.BindFlags = 0; desc.Usage = D3D11_USAGE_STAGING; desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            staging.Reset();
            check(device->CreateTexture2D(&desc, nullptr, &staging), "Readback target");
            readback_width = width; readback_height = height;
        }
        const UINT x = (input.width - width) / 2, y = (input.height - height) / 2;
        D3D11_BOX box{x, y, 0, x + width, y + height, 1};
        context->CopySubresourceRegion(staging.Get(), 0, 0, 0, 0, scene.texture.Get(), 0, &box);
        D3D11_MAPPED_SUBRESOURCE mapped{}; check(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped), "Readback");
        std::vector<char> bytes(width * height * 4);
        for (UINT row = 0; row < height; ++row)
            memcpy(bytes.data() + row * width * 4, static_cast<char*>(mapped.pData) + row * mapped.RowPitch, width * 4);
        context->Unmap(staging.Get(), 0);
        return bytes;
    }
};

int wmain(int argc, wchar_t** argv)
{
    _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
    try {
        if (argc != 2) throw std::runtime_error("Expected shader folder");
        Renderer renderer(argv[1]);
        Input input{};
        while (std::cin.read(reinterpret_cast<char*>(&input), sizeof(input))) {
            std::array<float, 36> values;
            memcpy(values.data(), input.mapping, sizeof(values));
            if (!std::all_of(values.begin(), values.end(), [](float value) { return std::isfinite(value); }) ||
                input.view[2] < 1 || input.view[3] < 1 || input.view[2] > 4096 || input.view[3] > 4096)
                throw std::runtime_error("Invalid preview parameters");
            uint32_t length = 0;
            if (!std::cin.read(reinterpret_cast<char*>(&length), 4)) throw std::runtime_error("Truncated path length");
            if (!length || length > 32768) throw std::runtime_error("Invalid path length");
            std::string path(length, '\0');
            if (!std::cin.read(path.data(), length)) throw std::runtime_error("Truncated path");
            auto bytes = renderer.render(input, fs::u8path(path));
            uint32_t size = uint32_t(bytes.size());
            std::cout.write(reinterpret_cast<char*>(&size), 4);
            std::cout.write(bytes.data(), bytes.size()); std::cout.flush();
        }
        if (std::cin.gcount() != 0) throw std::runtime_error("Truncated preview packet");
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
